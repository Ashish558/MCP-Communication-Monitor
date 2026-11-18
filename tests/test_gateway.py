"""Gateway tests: real aiohttp server in a thread, real stdio fake server, in-process daemon."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pytest
from aiohttp import web

from conftest import FAKE_SERVER, write_config
from mcp_monitor.config import Settings
from mcp_monitor.gateway import create_app
from mcp_monitor.gateway.upstream import HttpUpstream, StdioUpstream

TOKEN = "t" * 32


class Served:
    """Runs an aiohttp app on a background thread bound to a free port."""

    def __init__(self, app: web.Application):
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(app,), daemon=True)
        self.thread.start()
        assert self.ready.wait(10)

    def _run(self, app):
        asyncio.set_event_loop(self.loop)
        self.runner = web.AppRunner(app, access_log=None, handler_cancellation=True)
        self.loop.run_until_complete(self.runner.setup())
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        self.loop.run_until_complete(site.start())
        self.port = site._server.sockets[0].getsockname()[1]
        self.ready.set()
        self.loop.run_forever()

    def stop(self):
        asyncio.run_coroutine_threadsafe(self.runner.cleanup(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


def http(method, url, body=None, headers=None):
    """-> (status, headers, parsed JSON or text)"""
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            status, hdrs, raw = resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as err:
        status, hdrs, raw = err.code, err.headers, err.read()
    try:
        return status, hdrs, json.loads(raw) if raw else None
    except ValueError:
        return status, hdrs, raw.decode()


class Conn:
    """A Claude-like client: initialises, keeps the session id, makes calls."""

    def __init__(self, base: str, headers=None):
        self.base, self.headers, self.sid, self._n = base, headers or {}, None, 0

    def post(self, msg, **kw):
        hdrs = {**self.headers, **({"Mcp-Session-Id": self.sid} if self.sid else {}), **kw.pop("headers", {})}
        return http("POST", self.base, msg, hdrs)

    def initialize(self):
        status, hdrs, body = http("POST", self.base, {
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "claude-ai", "version": "1"}}}, self.headers)
        assert status == 200, body
        self.sid = hdrs["Mcp-Session-Id"]
        self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return body

    def call(self, name, arguments=None, rpc_id=None):
        self._n += 1
        return self.post({"jsonrpc": "2.0", "id": rpc_id or self._n, "method": "tools/call",
                          "params": {"name": name, "arguments": arguments or {}}})


@pytest.fixture()
def gw(daemon, home):
    served = []

    def make(upstream=None, **kwargs):
        settings = Settings.load(home)
        up = upstream or StdioUpstream([sys.executable, FAKE_SERVER], request_timeout=15)
        app = create_app(up, settings, "web-fake", TOKEN, **kwargs)
        s = Served(app)
        served.append(s)
        return s, f"http://127.0.0.1:{s.port}/mcp/{TOKEN}"

    yield make
    for s in served:
        s.stop()


def text_of(body):
    return body["result"]["content"][0]["text"]


def test_initialize_returns_session_and_server_info(gw):
    _, url = gw()
    c = Conn(url)
    assert c.initialize()["result"]["serverInfo"]["name"] == "fake"
    assert c.sid


def test_allowed_call_reaches_server_and_popup_has_details(gw, daemon):
    _, url = gw()
    c = Conn(url)
    c.initialize()
    status, _, body = c.call("echo", {"text": "allow-me from web"})
    assert status == 200 and text_of(body) == "echo:allow-me from web"
    req = daemon.presenter.shown[0]
    assert req.server == "web-fake" and req.tool == "echo"
    assert req.client == "claude-web (claude-ai 1)"
    assert "allow-me from web" in req.arguments_preview


def test_blocked_call_never_reaches_server(gw):
    _, url = gw()
    c = Conn(url)
    c.initialize()
    _, _, body = c.call("echo", {"text": "block-me"})
    assert body["result"]["isError"] is True and "Blocked by the user" in text_of(body)
    assert text_of(c.call("count", {"x": "allow-me"})[2]) == "0"


def test_tools_list_and_notifications_do_not_prompt(gw, daemon):
    _, url = gw()
    c = Conn(url)
    c.initialize()
    status, _, body = c.post({"jsonrpc": "2.0", "id": 5, "method": "tools/list"})
    assert status == 200 and len(body["result"]["tools"]) == 5
    status, _, _ = c.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert status == 202
    assert daemon.presenter.shown == []


def test_wrong_token_is_404_and_reveals_nothing(gw):
    s, url = gw()
    bad = f"http://127.0.0.1:{s.port}/mcp/not-the-token-at-all-xxxxxxxxxx"
    assert http("POST", bad, {"jsonrpc": "2.0", "id": 1, "method": "ping"})[0] == 404
    assert http("POST", f"http://127.0.0.1:{s.port}/", {})[0] == 404
    assert http("GET", bad)[0] == 404


def test_browser_origins_are_rejected_claude_origin_accepted(gw):
    _, url = gw()
    evil = Conn(url, {"Origin": "https://evil.example"})
    status, _, _ = http("POST", url, {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
                        {"Origin": "https://evil.example"})
    assert status == 403
    ok = Conn(url, {"Origin": "https://claude.ai"})
    assert ok.initialize()["result"]["serverInfo"]["name"] == "fake"
    del evil


def test_get_is_405_and_unknown_session_is_404(gw):
    _, url = gw()
    assert http("GET", url)[0] == 405
    status, _, _ = http("POST", url, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                        {"Mcp-Session-Id": "nope"})
    assert status == 404
    status, _, body = http("POST", url, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert status == 400 and "Mcp-Session-Id" in body["error"]["message"]


def test_batches_are_refused_not_silently_allowed(gw):
    _, url = gw()
    c = Conn(url)
    c.initialize()
    status, _, body = c.post([{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "echo", "arguments": {}}}])
    assert status == 400 and "Batch" in body["error"]["message"]


def test_bad_json_and_oversized_body(gw):
    _, url = gw()
    assert http("POST", url, b"{not json")[0] == 400
    assert http("POST", url, b"x" * (11 * 1024 * 1024))[0] == 413


def test_concurrent_calls_each_get_their_own_decision(gw):
    _, url = gw()
    c = Conn(url)
    c.initialize()

    def one(i):
        verdict = "allow-me" if i % 2 == 0 else "block-me"
        return verdict, c.call("echo", {"text": f"{verdict} {i}"}, rpc_id=100 + i)[2]

    with ThreadPoolExecutor(16) as pool:
        results = list(pool.map(one, range(24)))
    for verdict, body in results:
        assert bool(body["result"].get("isError")) == (verdict == "block-me")


def test_two_sessions_are_isolated(gw):
    _, url = gw()
    a, b = Conn(url), Conn(url)
    a.initialize(), b.initialize()
    assert a.sid != b.sid
    assert text_of(a.call("echo", {"text": "allow-me A"}, rpc_id=1)[2]) == "echo:allow-me A"
    assert b.call("echo", {"text": "block-me B"}, rpc_id=1)[2]["result"]["isError"] is True


def test_unanswered_request_times_out_to_block(gw):
    _, url = gw()
    c = Conn(url)
    c.initialize()
    start = time.time()
    _, _, body = c.call("echo", {"text": "ignore-me"})
    assert body["result"]["isError"] is True and "No answer" in text_of(body)
    assert 2.5 < time.time() - start < 9


def test_client_hanging_up_withdraws_the_pending_request(gw, daemon):
    s, url = gw()
    c = Conn(url)
    c.initialize()
    payload = json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                          "params": {"name": "echo", "arguments": {"text": "ignore-me"}}}).encode()
    sock = socket.create_connection(("127.0.0.1", s.port))
    sock.sendall((f"POST /mcp/{TOKEN} HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                  f"Mcp-Session-Id: {c.sid}\r\nContent-Length: {len(payload)}\r\n\r\n").encode() + payload)
    deadline = time.time() + 5
    while not daemon.broker.pending and time.time() < deadline:
        time.sleep(0.05)
    assert daemon.broker.pending                      # popup is open
    sock.close()                                      # Claude gives up
    deadline = time.time() + 5
    while daemon.broker.pending and time.time() < deadline:
        time.sleep(0.05)
    assert daemon.broker.pending == {}                # popup dismissed, nothing left waiting


def test_explicit_cancel_notification_withdraws_request(gw, daemon):
    _, url = gw()
    c = Conn(url)
    c.initialize()
    with ThreadPoolExecutor(1) as pool:
        fut = pool.submit(c.call, "echo", {"text": "ignore-me"}, 77)
        deadline = time.time() + 5
        while not daemon.broker.pending and time.time() < deadline:
            time.sleep(0.05)
        status, _, _ = c.post({"jsonrpc": "2.0", "method": "notifications/cancelled",
                               "params": {"requestId": 77}})
        assert status == 202
        _, _, body = fut.result(10)
    assert "cancelled" in text_of(body).lower()
    assert daemon.broker.pending == {}


def test_delete_closes_session(gw):
    _, url = gw()
    c = Conn(url)
    c.initialize()
    assert http("DELETE", url, headers={"Mcp-Session-Id": c.sid})[0] == 200
    assert c.call("echo", {"text": "allow-me"})[0] == 404


def test_server_crash_returns_error_not_hang(gw):
    _, url = gw()
    c = Conn(url)
    c.initialize()
    status, _, body = c.call("crash", {"x": "allow-me"})
    assert status == 200 and "exited" in body["error"]["message"]
    status, _, body = c.call("echo", {"text": "allow-me"})
    assert "error" in body                            # later calls fail cleanly too


def test_fails_closed_when_daemon_is_down(home):
    write_config(home, decision_timeout=2)            # no daemon running, autostart off
    app = create_app(StdioUpstream([sys.executable, FAKE_SERVER]), Settings.load(home), "web-fake", TOKEN)
    s = Served(app)
    try:
        c = Conn(f"http://127.0.0.1:{s.port}/mcp/{TOKEN}")
        c.initialize()
        _, _, body = c.call("echo", {"text": "hello"})
        assert body["result"]["isError"] is True and "not running" in text_of(body)
        assert c.post({"jsonrpc": "2.0", "id": 5, "method": "ping"})[2]["result"] == {}
    finally:
        s.stop()


# --------------------------------------------------------------------------- HTTP upstream
@pytest.fixture()
def fake_http_upstream():
    seen = {"calls": [], "headers": []}

    async def handler(request):
        msg = await request.json()
        seen["headers"].append(dict(request.headers))
        if msg.get("method") == "initialize":
            return web.json_response({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "remote", "version": "1"}}},
                headers={"Mcp-Session-Id": "up-1"})
        if "id" not in msg:
            return web.Response(status=202)
        if msg["method"] == "tools/call":
            seen["calls"].append(msg["params"]["name"])
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": {
                "content": [{"type": "text", "text": "remote:" + msg["params"]["name"]}], "isError": False}}
            if msg["params"]["name"] == "stream":
                body = "event: message\ndata: " + json.dumps(reply) + "\n\n"
                return web.Response(body=body.encode(), content_type="text/event-stream")
            return web.json_response(reply)
        return web.json_response({"jsonrpc": "2.0", "id": msg["id"], "result": {}})

    app = web.Application()
    app.router.add_post("/mcp", handler)
    s = Served(app)
    yield f"http://127.0.0.1:{s.port}/mcp", seen
    s.stop()


def test_http_upstream_allow_block_and_header_passthrough(gw, fake_http_upstream):
    url_up, seen = fake_http_upstream
    _, url = gw(HttpUpstream(url_up, {"Authorization": "Bearer upstream-secret"}))
    c = Conn(url)
    assert c.initialize()["result"]["serverInfo"]["name"] == "remote"
    assert text_of(c.call("lookup", {"q": "allow-me"})[2]) == "remote:lookup"
    assert c.call("lookup", {"q": "block-me"})[2]["result"]["isError"] is True
    assert seen["calls"] == ["lookup"]                          # the blocked call never went upstream
    assert all(h.get("Authorization") == "Bearer upstream-secret" for h in seen["headers"])
    assert any(h.get("Mcp-Session-Id") == "up-1" for h in seen["headers"][1:])   # upstream session reused
    assert c.sid != "up-1"                                       # callers never see the upstream's session id


def test_http_upstream_sse_reply_is_streamed_through(gw, fake_http_upstream):
    url_up, _ = fake_http_upstream
    _, url = gw(HttpUpstream(url_up))
    c = Conn(url)
    c.initialize()
    status, hdrs, raw = c.call("stream", {"x": "allow-me"})
    assert status == 200 and hdrs["Content-Type"].startswith("text/event-stream")
    assert "remote:stream" in raw


def test_http_upstream_down_gives_clean_error(gw):
    served, url = gw(HttpUpstream("http://127.0.0.1:1/mcp"))      # nothing listens there
    status, hdrs, body = http("POST", url, {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}})
    assert status == 200 and "error" in body and "upstream" in body["error"]["message"]
    assert hdrs.get("Mcp-Session-Id") is None                     # no half-open session is handed out
