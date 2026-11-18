"""Browser bridge: lets the claude.ai browser extension ask the daemon for a decision.

Why this exists: connectors added directly in claude.ai (Figma, Gmail, ...) are run by
Anthropic's cloud, so no local proxy can see them. But claude.ai asks the user in the
browser ("Allow once / Decline") before it runs a connector tool, and the tool only runs
after the browser sends that approval. The extension holds that click, sends the details
here, and clicks Allow once or Decline according to the user's answer in *our* popup.

Endpoint (127.0.0.1 only):
  POST /decide   {"server":..., "tool":..., "details":..., "client":...}  ->  {"decision":"allow|block","reason":...}
  GET  /health   -> {"ok": true}
Security: bearer token (``bridge.json``), requests must come from a ``chrome-extension://``
origin (web pages are refused), size limits, fail closed.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import secrets
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from aiohttp import web

from .config import Settings, data_dir
from .gatekeeper import Gatekeeper
from .ipc import DaemonClient, new_ref

log = logging.getLogger("mcp_monitor.bridge")

MAX_BODY = 256 * 1024
MAX_FIELD = 100_000
DEFAULT_PORT = 8766


def load_token(home: Path | None = None, rotate: bool = False) -> str:
    home = home or data_dir()
    path = home / "bridge.json"
    if not rotate and path.is_file():
        try:
            token = json.loads(path.read_text(encoding="utf-8")).get("token")
            if isinstance(token, str) and len(token) >= 20:
                return token
        except (OSError, ValueError):
            pass
    home.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    path.write_text(json.dumps({"token": token}), encoding="utf-8")
    return token


class Bridge:
    def __init__(self, gate: Gatekeeper, client: DaemonClient, settings: Settings, token: str,
                 label: str = "claude.ai in browser") -> None:
        self.gate, self.client, self.settings, self.token, self.label = gate, client, settings, token, label
        self._pool = ThreadPoolExecutor(max_workers=settings.max_pending + 8, thread_name_prefix="bridge-decide")

    def build_app(self) -> web.Application:
        app = web.Application(client_max_size=MAX_BODY)
        app.router.add_post("/decide", self.decide)
        app.router.add_get("/health", self.health)
        app.on_startup.append(self._warm)
        app.on_cleanup.append(self._cleanup)
        return app

    async def _warm(self, app: web.Application) -> None:
        asyncio.get_running_loop().run_in_executor(None, self.client.warm_up)

    async def _cleanup(self, app: web.Application) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _guard(self, request: web.Request) -> None:
        origin = request.headers.get("Origin", "")
        if not origin.startswith("chrome-extension://"):        # refuses ordinary web pages
            raise web.HTTPForbidden(text="forbidden")
        supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), self.token.encode()):
            raise web.HTTPUnauthorized(text="bad token")

    async def health(self, request: web.Request) -> web.Response:
        self._guard(request)
        return web.json_response({"ok": True})

    async def decide(self, request: web.Request) -> web.Response:
        self._guard(request)
        try:
            body = await request.json()
            fields = {k: str(body.get(k, ""))[:MAX_FIELD] for k in ("server", "tool", "details", "client")}
        except (ValueError, AttributeError):
            return web.json_response({"decision": "block", "reason": "bad request"}, status=400)
        ref = new_ref()
        loop = asyncio.get_running_loop()
        try:
            decision = await loop.run_in_executor(
                self._pool, self.gate.ask_fields, ref,
                fields["client"] or self.label, fields["server"] or "unknown (claude.ai connector)",
                "tools/call (from claude.ai prompt)", fields["tool"] or "unknown", fields["details"])
        except asyncio.CancelledError:        # extension gave up / tab closed: withdraw the popup
            self.client.cancel(ref)
            raise
        verdict = "allow" if decision.verdict == "allow" else "block"
        return web.json_response({"decision": verdict, "reason": decision.reason})


def create_app(settings: Settings, token: str, client: DaemonClient | None = None) -> web.Application:
    client = client or DaemonClient(settings)
    gate = Gatekeeper(settings, client, "claude.ai connector")
    return Bridge(gate, client, settings, token).build_app()
