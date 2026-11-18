"""Where the gateway sends approved requests: a stdio server or an HTTP MCP URL.

Each MCP session gets its own upstream session. For stdio that means its own
child process (an MCP stdio server expects exactly one ``initialize``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable

from .. import protocol
from ..jobobject import KillOnCloseJob

log = logging.getLogger(__name__)


class UpstreamError(Exception):
    """The upstream server could not be reached or answered badly."""


@dataclass
class Reply:
    status: int = 200
    body: bytes = b""
    content_type: str = "application/json"
    stream: AsyncIterator[bytes] | None = None   # set for text/event-stream passthrough


# --------------------------------------------------------------------------- stdio
class StdioSession:
    def __init__(self, command: list[str], timeout: float, job: KillOnCloseJob) -> None:
        self._loop = asyncio.get_running_loop()
        self._timeout = timeout
        self._pending: dict[str, asyncio.Future] = {}
        self._write_lock = asyncio.Lock()
        self.dead = False
        exe = shutil.which(command[0]) or command[0]
        try:
            self._child = subprocess.Popen([exe, *command[1:]], stdin=subprocess.PIPE,
                                           stdout=subprocess.PIPE, bufsize=0)
        except OSError as exc:
            raise UpstreamError(f"cannot start MCP server {command[0]!r}: {exc}") from exc
        job.add(self._child)
        threading.Thread(target=self._read_loop, name="gw-stdio-out", daemon=True).start()

    def _read_loop(self) -> None:
        try:
            for line in iter(self._child.stdout.readline, b""):
                msg = protocol.parse_line(line)
                if protocol.is_response(msg):
                    self._loop.call_soon_threadsafe(self._deliver, msg)
                else:
                    log.debug("dropping server-initiated message (not supported over stdio bridge)")
        except (OSError, ValueError):
            pass
        self._loop.call_soon_threadsafe(self._on_exit)

    def _deliver(self, msg: dict) -> None:
        fut = self._pending.pop(protocol.id_key(msg["id"]), None)
        if fut and not fut.done():
            fut.set_result(msg)

    def _on_exit(self) -> None:
        self.dead = True
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(UpstreamError("the MCP server process exited"))
        self._pending.clear()

    async def _write(self, msg: dict) -> None:
        data = protocol.encode(msg)

        def write() -> None:
            self._child.stdin.write(data)
            self._child.stdin.flush()

        async with self._write_lock:
            try:
                await self._loop.run_in_executor(None, write)
            except (OSError, ValueError) as exc:
                self.dead = True
                raise UpstreamError("the MCP server process is not running") from exc

    async def post(self, msg: dict) -> Reply:
        if self.dead:
            raise UpstreamError("the MCP server process has exited")
        if not protocol.is_request(msg):          # notification or client response
            await self._write(msg)
            return Reply(status=202)
        key = protocol.id_key(msg["id"])
        if key in self._pending:
            raise UpstreamError("duplicate request id in this session")
        fut = self._loop.create_future()
        self._pending[key] = fut
        try:
            await self._write(msg)
            response = await asyncio.wait_for(fut, self._timeout)
        except asyncio.TimeoutError as exc:
            raise UpstreamError(f"the MCP server did not answer within {self._timeout:g}s") from exc
        finally:
            self._pending.pop(key, None)
        return Reply(body=json.dumps(response, ensure_ascii=False).encode("utf-8"))

    async def close(self) -> None:
        def stop() -> None:
            try:
                self._child.stdin.close()
            except OSError:
                pass
            try:
                self._child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._child.kill()
                self._child.wait()

        self.dead = True
        await self._loop.run_in_executor(None, stop)


class StdioUpstream:
    kind = "stdio"

    def __init__(self, command: list[str], request_timeout: float = 300.0) -> None:
        self.command = command
        self.request_timeout = request_timeout
        self._job = KillOnCloseJob()

    async def open(self) -> StdioSession:
        return StdioSession(self.command, self.request_timeout, self._job)

    async def aclose(self) -> None:
        pass


# --------------------------------------------------------------------------- http
class HttpSession:
    def __init__(self, upstream: "HttpUpstream") -> None:
        self._up = upstream
        self._sid: str | None = None
        self.dead = False

    async def post(self, msg: dict) -> Reply:
        import aiohttp

        http = self._up.http()
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   **self._up.headers}
        if self._sid:
            headers["Mcp-Session-Id"] = self._sid
        try:
            resp = await http.post(self._up.url, data=json.dumps(msg, ensure_ascii=False).encode("utf-8"),
                                   headers=headers)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise UpstreamError(f"upstream request failed: {exc.__class__.__name__}") from exc
        sid = resp.headers.get("Mcp-Session-Id")
        if sid and msg.get("method") == "initialize":
            self._sid = sid
        ctype = resp.headers.get("Content-Type", "application/json")
        if ctype.startswith("text/event-stream"):
            async def stream() -> AsyncIterator[bytes]:
                try:
                    async for chunk in resp.content.iter_any():
                        yield chunk
                finally:
                    resp.release()

            return Reply(status=resp.status, content_type="text/event-stream", stream=stream())
        try:
            body = await resp.read()
        finally:
            resp.release()
        return Reply(status=resp.status, body=body, content_type=ctype.split(";")[0])

    async def close(self) -> None:
        self.dead = True
        if self._sid:
            try:
                resp = await self._up.http().delete(self._up.url, headers={**self._up.headers,
                                                                          "Mcp-Session-Id": self._sid})
                resp.release()
            except Exception:
                pass


class HttpUpstream:
    kind = "http"

    def __init__(self, url: str, headers: dict[str, str] | None = None, timeout: float = 300.0) -> None:
        self.url = url
        self.headers = headers or {}
        self.timeout = timeout
        self._http = None

    def http(self):
        import aiohttp

        if self._http is None:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_read=self.timeout))
        return self._http

    async def open(self) -> HttpSession:
        return HttpSession(self)

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.close()


UpstreamFactory = Callable[[], Awaitable[object]]
