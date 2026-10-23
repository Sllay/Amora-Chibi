from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


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
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class CompactConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        return f"[{timestamp}] {record.levelname:<7} {record.name}: {record.getMessage()}"


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
