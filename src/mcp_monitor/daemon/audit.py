"""Append-only JSON-lines audit log with rotation."""

from __future__ import annotations

import json
import logging
import logging.handlers
import time
from pathlib import Path


class AuditLog:
    """Writes one JSON object per line. Never raises: logging must not break decisions."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._logger = logging.getLogger(f"mcp_monitor.audit.{id(self)}")
        self._logger.propagate = False
        self._logger.setLevel(logging.INFO)
        self._handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        self._handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.addHandler(self._handler)

    def write(self, event: dict) -> None:
        try:
            event = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **event}
            self._logger.info(json.dumps(event, ensure_ascii=False, default=str))
        except Exception:
            pass

    def close(self) -> None:
        self._logger.removeHandler(self._handler)
        self._handler.close()
