"""Güvenli logging kurulumu; secret/cookie/token basılmaz."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
SECRET_HINTS = (
    "cookie",
    "authorization",
    "token",
    "password",
    "secret",
    "session",
    "x-api-key",
    "x-ekap-sec",
    "set-cookie",
)


def setup_logging(level: int = logging.INFO, log_file: Optional[Path] = None) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
    if not root.handlers:
        stream = logging.StreamHandler(sys.stdout)
        stream.setLevel(level)
        stream.setFormatter(formatter)
        root.addHandler(stream)
    else:
        for handler in root.handlers:
            handler.setLevel(level)

    path = log_file or Path(os.environ.get("EKAP_LOG_FILE") or "logs/ekap.log")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not any(
            isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", "") == str(path.resolve())
            for h in root.handlers
        ):
            file_handler = logging.FileHandler(path, encoding="utf-8")
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
    except OSError:
        pass


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def is_sensitive_key(name: str) -> bool:
    lowered = (name or "").strip().lower()
    return any(hint in lowered for hint in SECRET_HINTS)


def redact_value(key: str, value: Optional[str]) -> str:
    if value is None:
        return ""
    if is_sensitive_key(key):
        return "<redacted>"
    text = str(value)
    if len(text) > 300:
        return text[:300] + "…"
    return text
