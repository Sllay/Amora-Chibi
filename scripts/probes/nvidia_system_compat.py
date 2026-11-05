from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
MODEL = "mistralai/mistral-large-4-1t-a52b"
ASSET_PATH = Path("jailbreak/Mistral-Jailbreak.md")
EXPECTED_SHA256 = "18d8e61bad0863cf8103eb6b90129f1db96e38b5901599684dee800404d9027b"


def check_asset() -> None:
    data = ASSET_PATH.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != EXPECTED_SHA256:
        raise RuntimeError("ASSET_INTEGRITY_FAIL")
    print("ASSET_INTEGRITY_OK: 20884 bytes; prompt content was NOT sent to provider")
    if len(data) != 20884:
        raise RuntimeError("ASSET_SIZE_MISMATCH")


def probe(label: str, messages: list[dict[str, str]], key: str) -> dict[str, object]:
    body = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.5,
        "top_p": 1.0,
        "reasoning_effort": "low",
        "max_tokens": 4096,
        "stream": True,
    }
    request = Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    status = None
    chars = 0
    finish = "none"
    started = time.monotonic()
    try:
        with urlopen(request, timeout=170) as response:
            status = response.status
            if "text/event-stream" not in response.headers.get("Content-Type", ""):
                raise RuntimeError("UNEXPECTED_CONTENT_TYPE")
            for line in response:
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    break
                if not data:
                    continue
                event = json.loads(data)
                if event.get("error"):
                    raise RuntimeError("PROVIDER_STREAM_ERROR")
                for choice in event.get("choices", []):
                    delta = choice.get("delta") or {}
                    piece = delta.get("content")
                    if isinstance(piece, str):
                        chars += len(piece)
                    if choice.get("finish_reason"):
                        finish = str(choice["finish_reason"])
                if finish != "none":
                    break
        result = {"probe": label, "http_status": status, "visible_chars": chars,
                  "finish": finish, "elapsed_seconds": round(time.monotonic() - started, 1)}
        print("RESULT " + json.dumps(result, sort_keys=True))
        return result
    except HTTPError as err:
        result = {"probe": label, "http_status": err.code,
                  "error": "HTTP_ERROR", "elapsed_seconds": round(time.monotonic() - started, 1)}
        print("RESULT " + json.dumps(result, sort_keys=True))
        return result
    except (URLError, TimeoutError, OSError, ValueError, RuntimeError) as err:
        # Do not print exception messages: upstream could echo sensitive request context.
        result = {"probe": label, "http_status": status, "error": type(err).__name__,
                  "elapsed_seconds": round(time.monotonic() - started, 1)}
        print("RESULT " + json.dumps(result, sort_keys=True))
        return result


def main() -> int:
    check_asset()
    key = os.environ.get("NVIDIA_API_KEY", "")
    if not key:
        print("ERROR: NVIDIA_API_KEY_MISSING")
        return 2

    two = probe("two_system_messages", [
        {"role": "system", "content": "You are a concise, harmless protocol-compatibility probe."},
        {"role": "system", "content": "Respond with the single ASCII word OK."},
        {"role": "user", "content": "Return OK."},
    ], key)
    one = probe("one_system_message", [
        {"role": "system", "content": "You are a concise compatibility probe. Respond with OK."},
        {"role": "user", "content": "Return OK."},
    ], key)
    accepted_two = two.get("http_status") == 200
    accepted_one = one.get("http_status") == 200
    print("SUMMARY " + json.dumps({
        "two_system_http_accepted": accepted_two,
        "one_system_http_accepted": accepted_one,
        "note": "Neutral protocol test only; does not test the original prompt or model filtering.",
    }))
    return 0 if accepted_one or accepted_two else 1


if __name__ == "__main__":
    sys.exit(main())
