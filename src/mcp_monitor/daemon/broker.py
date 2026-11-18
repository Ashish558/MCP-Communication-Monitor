"""Broker: the daemon's decision engine.

Accepts authenticated connections from shims, turns each ``request`` message
into a :class:`PendingRequest`, asks the *presenter* (popup UI) to show it, and
returns the user's decision. It owns timeouts, cancellation and cleanup, so the
UI only has to call :meth:`Broker.resolve`.

Everything here runs on one asyncio loop (in a background thread);
:meth:`Broker.resolve` is thread-safe so the Tk thread can call it.
"""

from __future__ import annotations

import asyncio
import hmac
import itertools
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Protocol

from ..config import Settings
from ..ipc import MAX_LINE
from .audit import AuditLog

log = logging.getLogger(__name__)


class Presenter(Protocol):
    """What the broker needs from a UI. All methods may be called from any thread."""

    def attach(self, broker: "Broker") -> None: ...
    def show(self, req: "PendingRequest") -> None: ...
    def close(self, req_id: int, outcome: str) -> None: ...
    def pending_changed(self, count: int) -> None: ...


@dataclass
class PendingRequest:
    id: int
    ref: str
    client: str
    server: str
    method: str
    tool: str
    arguments_preview: str
    arguments_size: int
    created: float
    deadline: float
    future: asyncio.Future = field(repr=False, default=None)  # type: ignore[assignment]
    conn: "_Conn" = field(repr=False, default=None)           # type: ignore[assignment]


class _Conn:
    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self.writer = writer
        self.lock = asyncio.Lock()
        self.tasks: set[asyncio.Task] = set()
        self.by_ref: dict[str, PendingRequest] = {}

    async def send(self, msg: dict) -> None:
        async with self.lock:
            try:
                self.writer.write(json.dumps(msg, ensure_ascii=False).encode("utf-8") + b"\n")
                await self.writer.drain()
            except (ConnectionError, OSError):
                pass


class Broker:
    def __init__(self, presenter: Presenter, audit: AuditLog, settings: Settings, token: str) -> None:
        self.presenter = presenter
        self.audit = audit
        self.settings = settings
        self.token = token
        self.loop: asyncio.AbstractEventLoop | None = None
        self.pending: dict[int, PendingRequest] = {}
        self._seq = itertools.count(1)
        self._server: asyncio.AbstractServer | None = None
        self._conns: set[_Conn] = set()
        self._closing = False

    # ------------------------------------------------------------------ server
    async def start(self, host: str = "127.0.0.1") -> int:
        self.loop = asyncio.get_running_loop()
        self._server = await asyncio.start_server(self._on_connection, host, 0, limit=MAX_LINE)
        return self._server.sockets[0].getsockname()[1]

    async def shutdown(self) -> None:
        """Block everything still pending and close all connections."""
        self._closing = True
        if self._server:
            self._server.close()
        for req in list(self.pending.values()):
            self._resolve(req.id, "block", "the monitor is shutting down")
        await asyncio.sleep(0.2)  # let the decisions reach the shims
        for conn in list(self._conns):
            conn.writer.close()

    # ------------------------------------------------------------------ decisions
    def resolve(self, req_id: int, verdict: str, reason: str = "") -> None:
        """Thread-safe: called by the UI when the user clicks Allow / Block."""
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self._resolve, req_id, verdict, reason)

    def _resolve(self, req_id: int, verdict: str, reason: str) -> None:
        req = self.pending.get(req_id)
        if req and req.future is not None and not req.future.done():
            req.future.set_result((verdict, reason))   # first decision wins

    # ------------------------------------------------------------------ connections
    async def _on_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = _Conn(writer)
        self._conns.add(conn)
        try:
            first = await asyncio.wait_for(reader.readline(), 5)
            hello = json.loads(first or b"{}")
            if hello.get("type") != "hello" or not hmac.compare_digest(
                    str(hello.get("token", "")), self.token):
                self.audit.write({"event": "auth_failed"})
                return
            await conn.send({"type": "hello_ok"})
            while True:
                line = await reader.readline()
                if not line:
                    break
                await self._handle(conn, json.loads(line))
        except (asyncio.TimeoutError, ConnectionError, OSError, ValueError):
            pass  # includes oversized lines and garbage: just drop the connection
        except Exception:
            log.exception("connection handler crashed")
        finally:
            for task in list(conn.tasks):
                task.cancel()    # shim vanished: its pending requests are abandoned
            self._conns.discard(conn)
            writer.close()

    async def _handle(self, conn: _Conn, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "request":
            task = asyncio.create_task(self._process(conn, msg))
            conn.tasks.add(task)
            task.add_done_callback(conn.tasks.discard)
        elif kind == "cancel":
            req = conn.by_ref.get(str(msg.get("ref")))
            if req:
                self._resolve(req.id, "cancelled", "cancelled by the client")
        elif kind == "event":
            self.audit.write({k: v for k, v in msg.items() if k != "type"})
        elif kind == "ping":
            await conn.send({"type": "pong", "pending": len(self.pending)})

    async def _process(self, conn: _Conn, msg: dict) -> None:
        ref = str(msg.get("ref"))
        now = time.time()
        req = PendingRequest(
            id=next(self._seq), ref=ref,
            client=str(msg.get("client", "unknown")), server=str(msg.get("server", "unknown")),
            method=str(msg.get("method", "")), tool=str(msg.get("tool", "")),
            arguments_preview=str(msg.get("arguments_preview", "")),
            arguments_size=int(msg.get("arguments_size") or 0),
            created=now, deadline=now + self.settings.decision_timeout,
            future=asyncio.get_running_loop().create_future(), conn=conn,
        )
        base = {"id": req.id, "client": req.client, "server": req.server,
                "method": req.method, "tool": req.tool, "arguments": req.arguments_preview}

        if self._closing or len(self.pending) >= self.settings.max_pending:
            await conn.send({"type": "decision", "ref": ref, "decision": "block",
                             "reason": "Too many requests are waiting for approval."})
            self.audit.write({"event": "rejected_overload", **base})
            return

        self.pending[req.id] = req
        conn.by_ref[ref] = req
        self.audit.write({"event": "requested", **base})
        self._notify_count()
        verdict, reason = "block", "no decision"
        try:
            try:
                self.presenter.show(req)
            except Exception as exc:
                log.exception("presenter failed to show request")
                self._resolve(req.id, "block", f"the approval popup could not be shown ({exc.__class__.__name__})")
            try:
                verdict, reason = await asyncio.wait_for(asyncio.shield(req.future), self.settings.decision_timeout)
            except asyncio.TimeoutError:
                verdict, reason = "block", f"No answer within {self.settings.decision_timeout:g} seconds."
            await conn.send({"type": "decision", "ref": ref, "decision": verdict, "reason": reason})
        except asyncio.CancelledError:
            verdict, reason = "abandoned", "the shim disconnected before a decision"
            raise
        finally:
            self.pending.pop(req.id, None)
            conn.by_ref.pop(ref, None)
            if not req.future.done():
                req.future.cancel()
            try:
                self.presenter.close(req.id, verdict)
            except Exception:
                log.exception("presenter close failed")
            self.audit.write({"event": "decided", "decision": verdict, "reason": reason,
                              "waited_s": round(time.time() - now, 3), **base})
            self._notify_count()

    def _notify_count(self) -> None:
        try:
            self.presenter.pending_changed(len(self.pending))
        except Exception:
            pass
