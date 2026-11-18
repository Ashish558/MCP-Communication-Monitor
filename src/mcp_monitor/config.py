"""Settings and file locations.

Settings come from ``<home>/config.json`` and can be overridden by
environment variables (useful for the emergency off switch):

* ``MCP_MONITOR_HOME``       - change the data directory
* ``MCP_MONITOR_DISABLE=1``  - shims become plain pass-through (no popups)
* ``MCP_MONITOR_FAIL_MODE``  - ``closed`` (default) or ``open``
* ``MCP_MONITOR_TIMEOUT``    - seconds before an unanswered popup auto-blocks
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, fields
from pathlib import Path

APP_NAME = "mcp-monitor"
log = logging.getLogger(__name__)


def data_dir() -> Path:
    """Per-user directory for the daemon discovery file, logs and config."""
    override = os.environ.get("MCP_MONITOR_HOME")
    if override:
        return Path(override)
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        root = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(root) / APP_NAME


def project_backup_dir() -> Path:
    """``<repo>/backups/config`` when running from a source checkout, else ``<home>/backups``."""
    repo_root = Path(__file__).resolve().parents[2]
    if (repo_root / "pyproject.toml").is_file():          # running from a source checkout
        return repo_root / "backups" / "config"
    return data_dir() / "backups"


@dataclass
class Settings:
    fail_mode: str = "closed"          # what to do when the daemon cannot be reached
    decision_timeout: float = 60.0     # seconds before an unanswered request is blocked
    intercept_methods: tuple = ("tools/call",)
    max_pending: int = 100             # more than this at once -> extra requests auto-block
    max_visible_popups: int = 3        # popups shown at once; the rest wait in a queue
    autostart_daemon: bool = True      # shim starts the daemon if it is not running
    preview_max_chars: int = 4000      # parameter text shown in the popup / audit log
    log_arguments: bool = True         # False: the audit log records decisions but not parameters
    button_delay_ms: int = 600         # buttons are inert this long after a popup appears
    log_level: str = "INFO"
    disabled: bool = False             # pass-through mode (emergency off switch)

    @classmethod
    def load(cls, home: Path | None = None) -> "Settings":
        home = home or data_dir()
        values: dict = {}
        path = home / "config.json"
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                known = {f.name for f in fields(cls)}
                values = {k: v for k, v in raw.items() if k in known}
            except (OSError, ValueError) as exc:
                log.warning("Ignoring unreadable %s: %s", path, exc)
        settings = cls(**values)
        env = os.environ
        if env.get("MCP_MONITOR_DISABLE", "").strip() in ("1", "true", "yes"):
            settings.disabled = True
        if env.get("MCP_MONITOR_FAIL_MODE"):
            settings.fail_mode = env["MCP_MONITOR_FAIL_MODE"].strip().lower()
        if env.get("MCP_MONITOR_TIMEOUT"):
            try:
                settings.decision_timeout = float(env["MCP_MONITOR_TIMEOUT"])
            except ValueError:
                pass
        return settings.normalized()

    def normalized(self) -> "Settings":
        """Replace invalid values with safe defaults (never fail open by accident)."""
        if self.fail_mode not in ("closed", "open"):
            self.fail_mode = "closed"
        try:
            self.decision_timeout = float(self.decision_timeout)
        except (TypeError, ValueError):
            self.decision_timeout = 60.0
        if self.decision_timeout <= 0:
            self.decision_timeout = 60.0
        self.intercept_methods = tuple(str(m) for m in (self.intercept_methods or ("tools/call",)))
        self.max_pending = max(1, int(self.max_pending))
        self.max_visible_popups = max(1, int(self.max_visible_popups))
        return self
