"""Opt-in standalone Numes measurement; no AmoraCore, database or user context.

python -m amora.cognition.benchmark_numes --live --samples 3
Uses AMORA_SECRET_NUMES_API_KEY (or NUMES_API_KEY) from the environment.
Never outputs prompts, generated text, credentials or provider error bodies.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

from amora.latency_trace import bind_latency_trace
from .providers import ProviderError
from .providers_numes import NumesCognitiveProvider


class EnvironmentSecrets:
    def get(self, name):
        return os.getenv("AMORA_SECRET_" + name) or os.getenv(name)


def synthetic_payload(model, *, large):
    messages = [{"role": "system", "content":
                 "Responda somente JSON com o campo answer. O histórico abaixo é sintético. "
                 "No último pedido, escreva uma resposta curta em português brasileiro."}]
    if large:
        # Natural-ish synthetic turns, approximately the production byte volume.
        # Token counts depend on the provider tokenizer; use reported usage.
        for index in range(16):
            paragraph = (
                f"Exemplo fictício {index}: Em um jogo de exploração, três regiões têm "
                "climas diferentes. Os habitantes coletam recursos, constroem abrigos "
                "e planejam viagens. O terreno muda com chuva e erosão; os animais "
                "procuram alimento e água. Compare rotas, custos e possibilidades. "
            )
            messages.append({"role": "user" if index % 2 == 0 else "assistant",
                             "content": paragraph * 8})
    messages.append({"role": "user", "content":
                     "Explique em duas frases por que uma aldeia perto de um rio precisa planejar seus recursos. "
                     "Retorne somente JSON com uma string answer."})
    return {"model": model, "messages": messages, "stream": True, "store": False,
            "temperature": 0.65, "max_tokens": 1200,
            "response_format": {"type": "json_object"},
            "provider": {"max_price": {"prompt": 0, "completion": 0},
                         "data_collection": "deny", "zdr": True,
                         "require_parameters": True}}


async def measure(provider, *, samples=3):
    if not 1 <= samples <= 5:
        raise ValueError("samples must be between 1 and 5")
    key = provider.secrets.get("NUMES_API_KEY")
    if not key:
        raise ValueError("NUMES_API_KEY is not configured")
    results = []
    await provider.start_transport()
    try:
        for index in range(samples):
            for large in (False, True):
                payload = synthetic_payload(provider.model, large=large)
                with bind_latency_trace() as trace:
                    row = {"sample": index + 1, "profile": "large" if large else "small",
                           "payload_bytes": len(json.dumps(payload, ensure_ascii=False).encode())}
                    try:
                        result = await provider._complete_stream(payload, key=key, phase="synthetic_benchmark")
                        row.update(success=isinstance(result.get("answer"), str), status_code=200)
                    except ProviderError as exc:
                        row.update(success=False, status_code=exc.status_code,
                                   error_code=type(exc).__name__)
                    except Exception as exc:
                        row.update(success=False, error_code=type(exc).__name__)
                    row["metrics"] = {k: v for k, v in trace.items()
                                      if k.startswith("numes_") and type(v) in (int, float)}
                    results.append(row)
    finally:
        await provider.stop_transport()
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly allow real Numes requests")
    parser.add_argument("--samples", type=int, default=3, choices=range(1, 6))
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required for real provider requests")
    provider = NumesCognitiveProvider(EnvironmentSecrets(), timeout_seconds=75)
    try:
        rows = asyncio.run(measure(provider, samples=args.samples))
    except ValueError:
        parser.error("NUMES_API_KEY is not configured")
    print(json.dumps({"model": provider.model, "samples": rows}, ensure_ascii=False))


if __name__ == "__main__":
    main()
