"""Official API contract mocked; none of these tests benchmarks the real model."""

import hashlib
import io
import json
import math
from types import SimpleNamespace
import sys
import wave

import pytest

from amora.conversation.models import SpeechStyle
from amora.voice.audio_contract import encode_pcm16, validate_wav
from amora.voice.contract import RenderInput
from amora.voice.local_amora import LocalVoiceUnavailable
from amora.voice.omnivoice_renderer import (
    OmniVoiceRenderer,
    OmniVoiceSettings,
    map_cues,
)
from amora.voice.reference import OmniVoiceReferenceManifest, file_sha256


@pytest.fixture
def reference_assets(tmp_path):
    reference = tmp_path / "reference.wav"
    reference.write_bytes(
        encode_pcm16((0.2 * math.sin(i * 0.04) for i in range(72000)), 24000)
    )
    prompt = tmp_path / "prompt.pt"
    prompt.write_bytes(b"mocked-official-prompt-private-do-not-log")
    manifest = tmp_path / "manifest.json"
    data = {
        "version": 1,
        "voice_id": "LAB_TEST_NOT_CANONICAL",
        "reference_audio": "reference.wav",
        "reference_text": "Transcrição privada da referência.",
        "sha256": file_sha256(reference),
        "sample_rate": 24000,
        "duration_seconds": 3.0,
        "prompt_sha256": file_sha256(prompt),
    }
    manifest.write_text(json.dumps(data), encoding="utf-8")
    return manifest, prompt, reference, data


class FakeOfficialModel:
    sampling_rate = 24000
    config = SimpleNamespace(num_audio_codebook=8)

    def __init__(self):
        self.calls = []
        self.audio = [0.1 * math.sin(i * 0.1) for i in range(2400)]

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return [self.audio]


@pytest.fixture
def mocked_omni(reference_assets):
    manifest, prompt, _, data = reference_assets
    model = FakeOfficialModel()
    loads = {"model": 0, "prompt": 0}

    def load_model(settings):
        loads["model"] += 1
        return model

    def load_prompt(path):
        assert path == prompt
        loads["prompt"] += 1
        return SimpleNamespace(
            ref_text=data["reference_text"],
            ref_rms=0.1,
            ref_audio_tokens=SimpleNamespace(ndim=2, shape=(8, 100)),
        )

    renderer = OmniVoiceRenderer(
        OmniVoiceSettings(manifest, prompt),
        model_loader=load_model,
        prompt_loader=load_prompt,
    )
    return renderer, model, loads


def test_official_model_prompt_loaded_once_and_generation_has_no_reference_per_request(
    mocked_omni,
):
    renderer, model, loads = mocked_omni
    for _ in range(3):
        assert renderer.ready()
        result = renderer.render(
            RenderInput("tu é impossível", SpeechStyle(), ("LAUGHTER", "SIGH"))
        )
        assert validate_wav(result.wav_bytes) == (24000, 2400)
    assert loads == {"model": 1, "prompt": 1}
    assert len(model.calls) == 3
    call = model.calls[0]
    assert call["text"] == "[laughter] [sigh] tu é impossível"
    assert (
        call["num_step"] == 32
        and call["guidance_scale"] == 2.0
        and call["class_temperature"] == 0.0
    )
    assert call["language"] == "pt" and call["voice_clone_prompt"].ref_text
    assert not {"ref_audio", "ref_text", "load_asr", "instruct"} & call.keys()
    assert renderer.state()["dtype"] == "float32"
    assert renderer.identity.streaming_capable is False


def test_mapper_uses_only_official_tags_and_reports_unknown():
    mapped, unsupported = map_cues(
        "pai...",
        ("LAUGHTER", "SURPRISE", "SIGH", "DISSATISFACTION", "UNKNOWN", "LAUGHTER"),
    )
    assert mapped == "[laughter] [surprise-oh] [sigh] [dissatisfaction-hnn] pai..."
    assert unsupported == ("UNKNOWN",)


@pytest.mark.parametrize(
    "field,value",
    [
        ("sha256", "0" * 64),
        ("prompt_sha256", "0" * 64),
        ("duration_seconds", 4),
        ("sample_rate", 16000),
        ("reference_text", "   "),
        ("voice_id", "AMORA_VOICE_SIGNATURE_V1_SOFT"),
        ("reference_audio", "https://example.com/ref.wav"),
    ],
)
def test_invalid_manifest_prevents_readiness_before_loading(
    reference_assets, field, value
):
    manifest, prompt, _, data = reference_assets
    data[field] = value
    manifest.write_text(json.dumps(data), encoding="utf-8")

    def forbidden(*args):
        pytest.fail("must validate assets before loading a model/prompt")

    renderer = OmniVoiceRenderer(
        OmniVoiceSettings(manifest, prompt),
        model_loader=forbidden,
        prompt_loader=forbidden,
    )
    assert not renderer.ready()
    assert renderer.state()["last_error"] == "OMNIVOICE_RUNTIME_UNAVAILABLE"


@pytest.mark.parametrize("asset", ["manifest", "prompt", "reference"])
def test_asset_replacement_invalidates_readiness_without_reloading(
    reference_assets, mocked_omni, asset
):
    renderer, model, loads = mocked_omni
    assert renderer.ready()
    path = reference_assets[{"manifest": 0, "prompt": 1, "reference": 2}[asset]]
    path.write_bytes(b"corrupt-private-data")
    assert not renderer.ready()
    assert loads == {"model": 1, "prompt": 1}
    assert not model.calls


def test_prompt_transcript_must_match_manifest(reference_assets):
    manifest, prompt, _, _ = reference_assets
    renderer = OmniVoiceRenderer(
        OmniVoiceSettings(manifest, prompt),
        model_loader=lambda _: FakeOfficialModel(),
        prompt_loader=lambda _: SimpleNamespace(ref_text="different"),
    )
    assert not renderer.ready()


@pytest.mark.parametrize(
    "samples",
    [[], [math.nan], [math.inf], [-math.inf], [1.1], [-1.1], [1.0] * 100, [[0.1, 0.2]]],
)
def test_invalid_float_output_rejected_before_pcm16(mocked_omni, samples):
    renderer, model, _ = mocked_omni
    model.audio = samples
    with pytest.raises(LocalVoiceUnavailable):
        renderer.render(RenderInput("Oi", SpeechStyle()))


@pytest.mark.parametrize(
    "kind",
    [
        "truncated",
        "empty",
        "channels",
        "width",
        "rate",
        "too-long",
        "clipping",
        "riff-size",
    ],
)
def test_shared_wav_validator_rejects_invalid_contract(kind):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(2 if kind == "channels" else 1)
        wav.setsampwidth(1 if kind == "width" else 2)
        wav.setframerate(16000 if kind == "rate" else 24000)
        wav.writeframes(
            b"\xff\x7f" * 240
            if kind == "clipping"
            else b"\0\0" * (0 if kind == "empty" else 240)
        )
    body = output.getvalue()
    if kind == "truncated":
        body = body[:-4]
    if kind == "riff-size":
        body += b"junk"
    if kind == "too-long":
        # Duration limit checked before data read, without allocating large audio.
        import struct

        body = bytearray(body)
        struct.pack_into("<I", body, 40, 24000 * 2 * 121)
    with pytest.raises(LocalVoiceUnavailable, match="VOICE_INVALID_WAV"):
        validate_wav(body)


@pytest.mark.parametrize("kind", ["silence", "quiet", "clipping", "short", "long"])
def test_reference_acoustic_quality_bounds(reference_assets, kind):
    manifest_path, _, reference, data = reference_assets
    frames = 24000 * (2 if kind == "short" else 11 if kind == "long" else 3)
    amplitude = (
        1.0
        if kind == "clipping"
        else 0.0001
        if kind == "quiet"
        else 0
        if kind == "silence"
        else 0.2
    )
    buffer = io.BytesIO()
    import struct

    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(
            b"".join(
                struct.pack(
                    "<h",
                    round(
                        amplitude
                        * (1 if kind == "clipping" else math.sin(i * 0.1))
                        * 32767
                    ),
                )
                for i in range(frames)
            )
        )
    reference.write_bytes(buffer.getvalue())
    data["sha256"] = hashlib.sha256(reference.read_bytes()).hexdigest()
    with pytest.raises((ValueError, LocalVoiceUnavailable)):
        OmniVoiceReferenceManifest.model_validate(data).validate_reference(
            manifest_path
        )


def test_official_cpu_loader_exact_api_and_safe_prompt_loader(
    monkeypatch, reference_assets
):
    import amora.voice.omnivoice_renderer as module

    calls = []

    class Component:
        def to(self, **kwargs):
            calls.append(("to", kwargs))
            return self

        def eval(self):
            calls.append(("eval",))
            return self

    model = Component()
    model.audio_tokenizer, model.sampling_rate = Component(), 24000
    float32 = object()
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            float32=float32, set_num_threads=lambda n: calls.append(("threads", n))
        ),
    )

    def load(name, **kwargs):
        calls.append(("load", name, kwargs))
        return model

    def prompt_load(path, **kwargs):
        calls.append(("prompt", path, kwargs))
        return "prompt"

    monkeypatch.setitem(
        sys.modules,
        "omnivoice",
        SimpleNamespace(
            OmniVoice=SimpleNamespace(from_pretrained=load),
            VoiceClonePrompt=SimpleNamespace(load=prompt_load),
        ),
    )
    monkeypatch.setattr(module, "version", lambda _: "0.2.1")
    settings = OmniVoiceSettings(reference_assets[0], reference_assets[1])
    assert module.load_official_model(settings) is model
    assert (
        "load",
        "k2-fsa/OmniVoice",
        {"device_map": "cpu", "dtype": float32, "load_asr": False},
    ) in calls
    assert calls.count(("to", {"device": "cpu", "dtype": float32})) == 2
    assert module.load_official_prompt(settings.prompt) == "prompt"
    assert ("prompt", str(settings.prompt), {"map_location": "cpu"}) in calls


@pytest.mark.parametrize("steps", [16, 24, 32])
def test_controlled_step_settings(reference_assets, steps):
    assert (
        OmniVoiceSettings(
            reference_assets[0], reference_assets[1], num_step=steps
        ).num_step
        == steps
    )


def test_forbid_alternate_model_and_steps(reference_assets):
    for kwargs in [{"model": "other"}, {"num_step": 8}, {"threads": 5}]:
        with pytest.raises(ValueError):
            OmniVoiceSettings(reference_assets[0], reference_assets[1], **kwargs)
