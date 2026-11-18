"""Bridge tests: the endpoint the claude.ai browser extension talks to."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest

from conftest import write_config
from mcp_monitor.bridge import create_app
from mcp_monitor.config import Settings
from test_gateway import Served

TOKEN = "b" * 32
EXT = "chrome-extension://abcdefghijklmnop"


def post(port, body, origin=EXT, token=TOKEN, path="/decide", method="POST"):
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            return err.code, json.loads(raw)
        except ValueError:
            return err.code, raw.decode()


@pytest.fixture()
def bridge(daemon, home):
    s = Served(create_app(Settings.load(home), TOKEN))
    yield s.port
    s.stop()


def test_allow_and_block_come_from_the_popup_and_details_are_shown(bridge, daemon):
    status, body = post(bridge, {"server": "Figma", "tool": "get_design_context",
                                 "details": "fileKey allow-me LpQv6kb7", "client": "claude.ai (browser)"})
    assert status == 200 and body["decision"] == "allow"
    shown = daemon.presenter.shown[0]
    assert (shown.server, shown.tool) == ("Figma", "get_design_context")
    assert "LpQv6kb7" in shown.arguments_preview and shown.client == "claude.ai (browser)"
    status, body = post(bridge, {"server": "Figma", "tool": "t", "details": "block-me"})
    assert body["decision"] == "block"


def test_unanswered_request_times_out_to_block(bridge):
    start = time.time()
    status, body = post(bridge, {"server": "Figma", "tool": "t", "details": "ignore-me"})
    assert status == 200 and body["decision"] == "block" and "No answer" in body["reason"]
    assert time.time() - start < 9


def test_requests_from_web_pages_are_refused(bridge):
    assert post(bridge, {"tool": "t"}, origin="https://evil.example")[0] == 403
    assert post(bridge, {"tool": "t"}, origin=None)[0] == 403


def test_missing_or_wrong_token_is_refused(bridge):
    assert post(bridge, {"tool": "t"}, token=None)[0] == 401
    assert post(bridge, {"tool": "t"}, token="wrong-token-wrong-token-wrong")[0] == 401
    assert post(bridge, None, path="/health", method="GET")[0] == 200


def test_bad_and_oversized_bodies_fail_closed(bridge):
    assert post(bridge, b"{nope")[1]["decision"] == "block"
    assert post(bridge, b"x" * 300_000)[0] == 413


def test_secrets_in_details_are_masked_in_the_popup(bridge, daemon):
    post(bridge, {"server": "S", "tool": "t", "details": "allow-me key sk-abcdefghijklmnopqrstuv"})
    assert "sk-abcdefghijklmnopqrstuv" not in daemon.presenter.shown[0].arguments_preview


def test_fails_closed_when_daemon_is_down(home):
    write_config(home, decision_timeout=2)          # no daemon, autostart off
    s = Served(create_app(Settings.load(home), TOKEN))
    try:
        status, body = post(s.port, {"server": "S", "tool": "t", "details": "x"})
        assert status == 200 and body["decision"] == "block" and "not running" in body["reason"]
    finally:
        s.stop()
