from __future__ import annotations

import json
import logging
import os
import sys
import re
from threading import RLock
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


_SECRET_VALUES: set[str] = set()
_SECRET_LOCK = RLock()


def remember_secret(value: str | None) -> None:
    if value and len(value) >= 4:
        with _SECRET_LOCK:
            _SECRET_VALUES.add(value)


def redact(text: str) -> str:
    with _SECRET_LOCK:
        values = tuple(_SECRET_VALUES)
    for value in sorted(values, key=len, reverse=True):
        text = text.replace(json.dumps(value, ensure_ascii=False)[1:-1], "[REDACTED]")
        text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)(Bearer\s+)[^\s\"']+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)([?&](?:key|api_key|token|access_token)=)[^&\s\"']+", r"\1[REDACTED]", text)
    return text


_STANDARD = set(logging.LogRecord(None, 0, "", 0, "", (), None).__dict__)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD and key not in {"message", "asctime"} and not key.startswith("_"):
                try:
                    json.dumps(value)
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = repr(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


class CompactConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        return redact(f"[{timestamp}] {record.levelname:<7} {record.name}: {record.getMessage()}")


def configure_logging(log_dir: Path, *, level: int = logging.INFO) -> None:
    """Keep complete structured diagnostics on disk and console noise minimal.

    `amora serve` replaces the warning console handler with the live server
    dashboard. Non-server commands still get concise warnings/errors instead of
    one JSON object per INFO/access event.
    """

    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if getattr(root, "_amora_configured", False):
        return
    root.setLevel(level)

    file_handler = RotatingFileHandler(
        log_dir / "amora.jsonl", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(JsonFormatter())
    file_handler._amora_file = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)

    if os.getenv("AMORA_MODE") == "cloud":
        stdout = logging.StreamHandler(sys.stdout)
        stdout.setLevel(level)
        stdout.setFormatter(JsonFormatter())
        stdout._amora_console = True
        root.addHandler(stdout)
        setattr(root, "_amora_configured", True)
        return

    stderr = logging.StreamHandler()
    stderr.setLevel(logging.WARNING)
    stderr.setFormatter(CompactConsoleFormatter())
    stderr._amora_console = True  # type: ignore[attr-defined]
    root.addHandler(stderr)

    setattr(root, "_amora_configured", True)
