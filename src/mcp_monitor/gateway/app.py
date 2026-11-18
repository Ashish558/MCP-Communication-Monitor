"""The gateway web app (MCP Streamable HTTP, JSON replies).

Endpoint: ``POST /mcp/<secret-token>``. Anything else is a plain 404/405, so the
URL reveals nothing without the token.

Per request:
  1. token + Origin checks, body size limit;
  2. ``initialize`` opens a session (own upstream server) and returns ``Mcp-Session-Id``;
  3. a request in ``intercept_methods`` (``tools/call`` by default) is held until the
     user answers in the popup; Block returns a denial, the upstream never sees it;
  4. everything else is forwarded unchanged.

Not supported (answered with a clear error, never silently allowed): JSON-RPC
batches, and server-initiated messages / GET streams.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from aiohttp import web

from .. import protocol
from ..config import Settings, data_dir
from ..gatekeeper import Gatekeeper
from ..ipc import DaemonClient, new_ref
from .upstream import Reply, UpstreamError

log = logging.getLogger("mcp_monitor.gateway")

DEFAULT_ORIGINS = ("https://claude.ai", "https://claude.com")
MAX_BODY = 10 * 1024 * 1024
SWEEPER = web.AppKey("sweeper", asyncio.Task)


@dataclass
class _Session:
    id: str
    upstream: object
    client_name: str
    last_used: float = field(default_factory=time.monotonic)
    held: dict = field(default_factory=dict)   # id_key -> (daemon ref, cancelled flag)


def load_token(home: Path | None = None, rotate: bool = False) -> str:
    """The secret path segment. Stored in ``gateway.json``; created on first use."""
    home = home or data_dir()
    path = home / "gateway.json"
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


class Gateway:
    def __init__(self, upstream, gate: Gatekeeper, client: DaemonClient, settings: Settings, token: str,
                 label: str = "claude-web", allowed_origins=DEFAULT_ORIGINS, max_sessions: int = 10,
                 idle_timeout: float = 1800.0) -> None:
        self.upstream, self.gate, self.client, self.settings = upstream, gate, client, settings
        self.token, self.label = token, label
        self.allowed_origins = frozenset(allowed_origins)
        self.max_sessions, self.idle_timeout = max_sessions, idle_timeout
        self.sessions: dict[str, _Session] = {}
        self._pool = ThreadPoolExecutor(max_workers=settings.max_pending + 8, thread_name_prefix="gw-decide")
        self._inflight = 0

    # ------------------------------------------------------------------ app wiring
    def build_app(self) -> web.Application:
        app = web.Application(client_max_size=MAX_BODY)
        app.router.add_post("/mcp/{token}", self.post)
        app.router.add_delete("/mcp/{token}", self.delete)
        app.router.add_get("/mcp/{token}", self.get)
        app.on_startup.append(self._on_startup)
        app.on_cleanup.append(self._on_cleanup)
        return app

    async def _on_startup(self, app: web.Application) -> None:
        asyncio.get_running_loop().run_in_executor(None, self.client.warm_up)   # start the daemon early
        app[SWEEPER] = asyncio.create_task(self._sweep())

    async def _on_cleanup(self, app: web.Application) -> None:
        app[SWEEPER].cancel()
        for session in list(self.sessions.values()):
            await self._close_session(session)
        await self.upstream.aclose()
        self._pool.shutdown(wait=False, cancel_futures=True)

    async def _sweep(self) -> None:
        while True:
            await asyncio.sleep(60)
            cutoff = time.monotonic() - self.idle_timeout
            for session in [s for s in self.sessions.values() if s.last_used < cutoff]:
                log.info("closing idle session %s", session.id[:8])
                await self._close_session(session)

    async def _close_session(self, session: _Session) -> None:
        self.sessions.pop(session.id, None)
        try:
            await session.upstream.close()
        except Exception:
            log.exception("error closing session")

    # ------------------------------------------------------------------ checks / helpers
    def _guard(self, request: web.Request) -> None:
        if not hmac.compare_digest(request.match_info["token"].encode(), self.token.encode()):
            raise web.HTTPNotFound()
        origin = request.headers.get("Origin")
        if origin and origin not in self.allowed_origins:     # blocks browser/DNS-rebinding access
            log.warning("rejected request with Origin %r", origin)
            raise web.HTTPForbidden(text="origin not allowed")

    @staticmethod
    def _json(obj: dict, status: int = 200, headers: dict | None = None) -> web.Response:
        return web.Response(body=json.dumps(obj, ensure_ascii=False).encode("utf-8"), status=status,
                            content_type="application/json", headers=headers)

    def _error(self, rpc_id, code: int, message: str, status: int = 200) -> web.Response:
        return self._json(protocol.error_response(rpc_id, code, message), status)

    async def _respond(self, reply: Reply, request: web.Request, headers: dict | None = None):
        if reply.stream is not None:
            resp = web.StreamResponse(status=reply.status, headers={"Content-Type": "text/event-stream",
                                                                    "Cache-Control": "no-cache", **(headers or {})})
            await resp.prepare(request)
            async for chunk in reply.stream:
                await resp.write(chunk)
            await resp.write_eof()
            return resp
        return web.Response(body=reply.body, status=reply.status,
                            content_type=reply.content_type, headers=headers)

    # ------------------------------------------------------------------ handlers
    async def get(self, request: web.Request) -> web.Response:
        self._guard(request)
        return web.Response(status=405, headers={"Allow": "POST, DELETE"}, text="no server-initiated stream")

    async def delete(self, request: web.Request) -> web.Response:
        self._guard(request)
        session = self.sessions.get(request.headers.get("Mcp-Session-Id", ""))
        if not session:
            return web.Response(status=404)
        await self._close_session(session)
        return web.Response(status=200)

    async def post(self, request: web.Request):
        self._guard(request)
        if self._inflight >= self.settings.max_pending + 20:
            return web.Response(status=429, text="too many requests in flight")
        self._inflight += 1
        try:
            return await self._post(request)
        finally:
            self._inflight -= 1

    async def _post(self, request: web.Request):
        try:
            msg = json.loads(await request.read())
        except ValueError:
            return self._error(None, -32700, "Parse error", 400)
        if isinstance(msg, list):
            return self._error(None, -32600, "Batch requests are not supported by the MCP Monitor gateway.", 400)
        if not isinstance(msg, dict):
            return self._error(None, -32600, "Invalid request", 400)
        rpc_id = msg.get("id")

        sid = request.headers.get("Mcp-Session-Id")
        extra: dict = {}
        if msg.get("method") == "initialize" and protocol.is_request(msg):
            if len(self.sessions) >= self.max_sessions:
                return self._error(rpc_id, protocol.INTERNAL_ERROR_CODE, "Too many sessions.", 503)
            try:
                upstream_session = await self.upstream.open()
            except UpstreamError as exc:
                return self._error(rpc_id, protocol.INTERNAL_ERROR_CODE, str(exc), 502)
            info_name = protocol.client_name_from_initialize(msg)
            session = _Session(secrets.token_urlsafe(24), upstream_session,
                               f"{self.label} ({info_name})" if info_name else self.label)
            self.sessions[session.id] = session
            extra["Mcp-Session-Id"] = session.id
            self.client.event({"event": "session_start", "client": session.client_name,
                               "server": self.gate.server_name})
        else:
            session = self.sessions.get(sid or "")
            if session is None:
                if not sid:
                    return self._error(rpc_id, -32600, "Missing Mcp-Session-Id header.", 400)
                return web.Response(status=404, text="unknown or expired session")
        session.last_used = time.monotonic()

        if msg.get("method") == "notifications/cancelled" and self._cancel_held(session, msg):
            return web.Response(status=202)

        if self.gate.intercepts(msg):
            denial = await self._decide(session, msg)
            if denial is not None:
                return denial

        try:
            reply = await session.upstream.post(msg)
        except UpstreamError as exc:
            log.warning("upstream error: %s", exc)
            if "Mcp-Session-Id" in extra:        # initialize failed: do not leave a half-open session
                await self._close_session(session)
            if protocol.is_request(msg):
                return self._error(rpc_id, protocol.INTERNAL_ERROR_CODE, str(exc))
            return web.Response(status=502, text=str(exc))
        return await self._respond(reply, request, extra)

    # ------------------------------------------------------------------ interception
    def _cancel_held(self, session: _Session, msg: dict) -> bool:
        params = msg.get("params")
        if not isinstance(params, dict) or params.get("requestId") is None:
            return False
        entry = session.held.get(protocol.id_key(params["requestId"]))
        if not entry:
            return False
        ref, cancelled = entry
        cancelled.set()
        self.client.cancel(ref)
        return True

    async def _decide(self, session: _Session, msg: dict) -> web.Response | None:
        """Hold the request for the user's decision. Returns a response to send (a denial), or
        ``None`` when the request was allowed and should be forwarded."""
        key = protocol.id_key(msg["id"])
        ref, cancelled = new_ref(), asyncio.Event()
        session.held[key] = (ref, cancelled)
        loop = asyncio.get_running_loop()
        try:
            decision = await loop.run_in_executor(self._pool, self.gate.ask, ref, msg, session.client_name)
        except asyncio.CancelledError:      # Claude hung up while the popup was open
            self.client.cancel(ref)
            raise
        finally:
            session.held.pop(key, None)
        if cancelled.is_set() or decision.verdict == "cancelled":
            return self._json(protocol.denial_response(msg, "The request was cancelled."))
        if decision.verdict == "allow":
            return None
        return self._json(protocol.denial_response(msg, decision.reason))


def create_app(upstream, settings: Settings, server_name: str, token: str, label: str = "claude-web",
               client: DaemonClient | None = None, **kwargs) -> web.Application:
    client = client or DaemonClient(settings)
    gate = Gatekeeper(settings, client, server_name)
    return Gateway(upstream, gate, client, settings, token, label=label, **kwargs).build_app()
