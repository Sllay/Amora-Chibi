from __future__ import annotations

import json
import logging
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


def configure_logging(log_dir: Path, *, level: int = logging.INFO) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if getattr(root, "_amora_configured", False):
        return
    root.setLevel(level)
    formatter = JsonFormatter()

    stderr = logging.StreamHandler()
    stderr.setFormatter(formatter)
    root.addHandler(stderr)

    file_handler = RotatingFileHandler(
        log_dir / "amora.jsonl", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    setattr(root, "_amora_configured", True)
