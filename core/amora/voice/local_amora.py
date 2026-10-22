from __future__ import annotations

import ctypes
import importlib.util
import os
import struct
import threading
import time
import wave
from array import array
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from amora.conversation import SpeechUtterance
from amora.settings import RuntimePaths


MODEL_ID = "sherpa-onnx-supertonic-3-tts-int8-2026-05-11"
VOICE_ID = "AMORA_VOICE_SIGNATURE_V1_SOFT"
VOICE_STYLE_FILE = "amora_voice.bin"
NUM_STEPS = 5
REQUIRED_MODEL_FILES = (
    "duration_predictor.int8.onnx",
    "text_encoder.int8.onnx",
    "vector_estimator.int8.onnx",
    "vocoder.int8.onnx",
    "tts.json",
    "unicode_indexer.bin",
    VOICE_STYLE_FILE,
)


class LocalVoiceUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LocalVoiceAudio:
    wav_bytes: bytes
    sample_rate: int
    sample_count: int
    synthesis_seconds: float
    world_seconds: float

    @property
    def duration_seconds(self) -> float:
        return self.sample_count / float(self.sample_rate) if self.sample_rate > 0 else 0.0

    @property
    def total_rtf(self) -> float:
        duration = self.duration_seconds
        return (self.synthesis_seconds + self.world_seconds) / duration if duration > 0 else 0.0


class _WorldProcessor:
    def __init__(self, library_path: Path) -> None:
        self.library_path = library_path
        self._lib: ctypes.CDLL | None = None
        self._lock = threading.Lock()

    def _ensure_loaded(self) -> ctypes.CDLL:
        if self._lib is not None:
            return self._lib
        with self._lock:
            if self._lib is not None:
                return self._lib
            if not self.library_path.is_file():
                raise LocalVoiceUnavailable(f"WORLD local ausente: {self.library_path}")
            lib = ctypes.CDLL(str(self.library_path))
            fn = lib.amora_voice_process_f32
            fn.argtypes = [
                ctypes.POINTER(ctypes.c_float),
                ctypes.c_size_t,
                ctypes.c_int,
                ctypes.c_float,
                ctypes.c_float,
                ctypes.c_float,
                ctypes.c_float,
                ctypes.c_float,
                ctypes.POINTER(ctypes.c_float),
                ctypes.c_size_t,
            ]
            fn.restype = ctypes.c_int
            self._lib = lib
            return lib

    def process(
        self,
        samples: array,
        sample_rate: int,
        *,
        pitch_range_scale: float,
        f0_register_scale: float,
        energy_scale: float,
        softness: float,
        firmness: float,
    ) -> array:
        if samples.typecode != "f":
            raise TypeError("samples must be array('f')")
        if not samples:
            return array("f")
        lib = self._ensure_loaded()
        input_buffer = (ctypes.c_float * len(samples)).from_buffer(samples)
        output_buffer = (ctypes.c_float * len(samples))()
        result = lib.amora_voice_process_f32(
            input_buffer,
            len(samples),
            int(sample_rate),
            float(pitch_range_scale),
            float(f0_register_scale),
            float(energy_scale),
            float(softness),
            float(firmness),
            output_buffer,
            len(samples),
        )
        if result != len(samples):
            raise LocalVoiceUnavailable(f"WORLD local falhou: code={result}")
        return array("f", output_buffer)


class AmoraLocalVoiceRenderer:
    """Local canonical-candidate voice renderer for Web/Safari.

    Supertonic runs inside the Termux Python process through sherpa-onnx 1.13.8.
    The frozen 60/40 style is supplied by ``amora_voice.bin``. FINAL_B,
    VoiceSignature V1 Soft and bounded Emotion V2 are then applied by the same
    C++ processor used by Android, compiled as a Termux shared library.
    """

    SAMPLE_RATE = 24_000

    def __init__(self, paths: RuntimePaths) -> None:
        self.paths = paths
        self.model_dir = Path(
            os.getenv("AMORA_LOCAL_TTS_MODEL_DIR", str(paths.home / "models" / MODEL_ID))
        ).expanduser()
        self.world_library = Path(
            os.getenv("AMORA_WORLD_PROCESSOR_LIB", str(paths.home / "lib" / "libamora_voice_processor.so"))
        ).expanduser()
        self._engine = None
        self._engine_lock = threading.RLock()
        self._world = _WorldProcessor(self.world_library)
        self._last_error: str | None = None
        self._last_metrics: dict[str, float] | None = None

    @property
    def configured(self) -> bool:
        return (
            importlib.util.find_spec("sherpa_onnx") is not None
            and all((self.model_dir / name).is_file() for name in REQUIRED_MODEL_FILES)
            and self.world_library.is_file()
        )

    @property
    def available(self) -> bool:
        return self.configured

    def state(self) -> dict[str, object]:
        missing = [name for name in REQUIRED_MODEL_FILES if not (self.model_dir / name).is_file()]
        return {
            "provider": "amora_local_supertonic_world",
            "model": MODEL_ID,
            "voice": VOICE_ID,
            "sample_rate": self.SAMPLE_RATE,
            "num_steps": NUM_STEPS,
            "configured": self.configured,
            "available": self.available,
            "model_dir": str(self.model_dir),
            "world_library": str(self.world_library),
            "missing_model_files": missing,
            "python_runtime_present": importlib.util.find_spec("sherpa_onnx") is not None,
            "world_runtime_present": self.world_library.is_file(),
            "last_error": self._last_error,
            "last_metrics": self._last_metrics,
        }

    def ensure_available(self) -> None:
        if importlib.util.find_spec("sherpa_onnx") is None:
            raise LocalVoiceUnavailable("sherpa-onnx não está instalado no venv do Core")
        missing = [name for name in REQUIRED_MODEL_FILES if not (self.model_dir / name).is_file()]
        if missing:
            raise LocalVoiceUnavailable("modelo local incompleto: " + ", ".join(missing))
        if not self.world_library.is_file():
            raise LocalVoiceUnavailable(f"processador WORLD local ausente: {self.world_library}")

    def _ensure_engine(self):
        self.ensure_available()
        if self._engine is not None:
            return self._engine
        with self._engine_lock:
            if self._engine is not None:
                return self._engine
            import sherpa_onnx

            config = sherpa_onnx.OfflineTtsConfig(
                model=sherpa_onnx.OfflineTtsModelConfig(
                    supertonic=sherpa_onnx.OfflineTtsSupertonicModelConfig(
                        duration_predictor=str(self.model_dir / "duration_predictor.int8.onnx"),
                        text_encoder=str(self.model_dir / "text_encoder.int8.onnx"),
                        vector_estimator=str(self.model_dir / "vector_estimator.int8.onnx"),
                        vocoder=str(self.model_dir / "vocoder.int8.onnx"),
                        tts_json=str(self.model_dir / "tts.json"),
                        unicode_indexer=str(self.model_dir / "unicode_indexer.bin"),
                        voice_style=str(self.model_dir / VOICE_STYLE_FILE),
                    ),
                    debug=False,
                    num_threads=2,
                    provider="cpu",
                ),
                max_num_sentences=1,
                silence_scale=1.0,
            )
            if not config.validate():
                raise LocalVoiceUnavailable("configuração local do Supertonic foi rejeitada")
            engine = sherpa_onnx.OfflineTts(config)
            self._engine = engine
            return engine

    def render(self, item: SpeechUtterance) -> LocalVoiceAudio:
        with self._engine_lock:
            try:
                engine = self._ensure_engine()
                import sherpa_onnx

                expression = item.style.expression
                rate = float(expression.rate_scale if expression is not None else item.style.rate)
                pause = float(expression.pause_scale if expression is not None else 1.0)
                pitch_range = float(expression.pitch_range_scale if expression is not None else 1.0)
                register = float(expression.f0_register_scale if expression is not None else 1.0)
                energy = float(expression.energy_scale if expression is not None else 1.0)
                softness = float(expression.softness if expression is not None else 0.25)
                firmness = float(expression.firmness if expression is not None else 0.15)

                gen = sherpa_onnx.GenerationConfig()
                gen.sid = 0
                gen.num_steps = NUM_STEPS
                gen.speed = max(0.90, min(1.08, rate))
                gen.silence_scale = max(0.88, min(1.16, pause))
                gen.extra["lang"] = "pt"
                gen.extra["seed"] = "7"

                started = time.perf_counter()
                audio = engine.generate(item.text, gen)
                synthesis_seconds = time.perf_counter() - started
                samples = array("f", (float(value) for value in audio.samples))
                sample_rate = int(audio.sample_rate)
                if not samples or sample_rate <= 0:
                    raise LocalVoiceUnavailable("Supertonic retornou áudio vazio")

                world_started = time.perf_counter()
                processed = self._world.process(
                    samples,
                    sample_rate,
                    pitch_range_scale=pitch_range,
                    f0_register_scale=register,
                    energy_scale=energy,
                    softness=softness,
                    firmness=firmness,
                )
                world_seconds = time.perf_counter() - world_started

                volume = max(0.0, min(1.0, float(item.style.volume)))
                if volume != 1.0:
                    for index, value in enumerate(processed):
                        processed[index] = max(-0.99, min(0.99, value * volume))

                wav_bytes = _encode_pcm16_wav(processed, sample_rate)
                duration = len(processed) / float(sample_rate)
                total_rtf = (synthesis_seconds + world_seconds) / duration if duration > 0 else 0.0
                self._last_metrics = {
                    "synthesis_seconds": round(synthesis_seconds, 6),
                    "world_seconds": round(world_seconds, 6),
                    "audio_seconds": round(duration, 6),
                    "total_rtf": round(total_rtf, 6),
                }
                self._last_error = None
                return LocalVoiceAudio(
                    wav_bytes=wav_bytes,
                    sample_rate=sample_rate,
                    sample_count=len(processed),
                    synthesis_seconds=synthesis_seconds,
                    world_seconds=world_seconds,
                )
            except LocalVoiceUnavailable as exc:
                self._last_error = str(exc)[:320]
                raise
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"[:320]
                raise LocalVoiceUnavailable(self._last_error) from exc


def _encode_pcm16_wav(samples: array, sample_rate: int) -> bytes:
    pcm = array("h")
    for value in samples:
        clamped = max(-1.0, min(1.0, float(value)))
        pcm.append(int(round(clamped * (32767 if clamped >= 0 else 32768))))
    if struct.pack("=H", 1) != struct.pack("<H", 1):
        pcm.byteswap()
    output = BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(int(sample_rate))
        wav.writeframes(pcm.tobytes())
    return output.getvalue()
