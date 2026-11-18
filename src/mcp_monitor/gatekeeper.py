"""The one place that turns an MCP request into an Allow/Block decision.

Both entry points (stdio shim for Claude Desktop/Code, HTTP gateway for Claude
Web) call :meth:`Gatekeeper.ask`, so behaviour is identical: build a safe
preview, ask the daemon, and apply the fail mode when it cannot be reached.
"""

from __future__ import annotations

import logging

from . import protocol
from .config import Settings
from .ipc import DaemonClient, DaemonUnavailable, Decision

log = logging.getLogger(__name__)


class Gatekeeper:
    def __init__(self, settings: Settings, client: DaemonClient, server_name: str) -> None:
        self.settings = settings
        self.client = client
        self.server_name = server_name
        self.methods = frozenset() if settings.disabled else frozenset(settings.intercept_methods)

    def intercepts(self, msg: object) -> bool:
        """True if ``msg`` is a request that must be approved before it is forwarded."""
        return protocol.is_request(msg) and msg.get("method") in self.methods  # type: ignore[union-attr]

    def ask(self, ref: str, msg: dict, client_name: str) -> Decision:
        """Blocking: show the popup via the daemon and return the user's decision.

        Never raises for monitor problems: they become a block (fail closed) unless
        ``fail_mode`` is ``open``.
        """
        call = protocol.describe(msg)
        text, size = protocol.preview(call.arguments, self.settings.preview_max_chars)
        info = {
            "client": protocol.sanitize_text(client_name),
            "server": protocol.sanitize_text(self.server_name),
            "method": call.method,
            "tool": protocol.sanitize_text(call.name),
            "arguments_preview": text,
            "arguments_size": size,
        }
        return self._decide(ref, info)

    def ask_fields(self, ref: str, client: str, server: str, method: str, tool: str, details: str) -> Decision:
        """Like :meth:`ask`, for sources that only have display text (the browser extension reads
        the tool name and parameters from claude.ai's own approval prompt)."""
        text, size = protocol.preview_text(details, self.settings.preview_max_chars)
        info = {
            "client": protocol.sanitize_text(client),
            "server": protocol.sanitize_text(server),
            "method": protocol.sanitize_text(method),
            "tool": protocol.sanitize_text(tool),
            "arguments_preview": text,
            "arguments_size": size,
        }
        return self._decide(ref, info)

    def _decide(self, ref: str, info: dict) -> Decision:
        try:
            return self.client.decide(ref, info, timeout=self.settings.decision_timeout + 5)
        except DaemonUnavailable as exc:
            if self.settings.fail_mode == "open":
                log.warning("monitor unavailable (%s): allowing because fail_mode=open", exc)
                return Decision("allow", "monitor unavailable (fail-open)")
            log.warning("monitor unavailable (%s): blocking", exc)
            return Decision("block", "The monitor is not running, so the request was blocked for safety.")
        except Exception as exc:  # anything unexpected must not let a call through
            log.exception("decision failed")
            return Decision("block", f"internal monitor error ({exc.__class__.__name__})")
