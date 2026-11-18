"""End-to-end tests: real shim process + real fake MCP server + in-process daemon."""

from __future__ import annotations

import sys
import time

from conftest import FAKE_SERVER, result_text, write_config


def test_allowed_call_reaches_server(daemon, shim):
    s = shim()
    assert s.initialize()["result"]["serverInfo"]["name"] == "fake"
    rid = s.call("echo", {"text": "allow-me hello"})
    reply = s.recv_for([rid])[rid]
    assert result_text(reply) == "echo:allow-me hello"
    assert len(daemon.presenter.shown) == 1
    req = daemon.presenter.shown[0]
    assert (req.server, req.tool, req.method) == ("fake", "echo", "tools/call")
    assert req.client == "pytest (pytest-client 1)"       # configured label + initialize clientInfo
    assert "allow-me hello" in req.arguments_preview      # parameters are captured


def test_blocked_call_never_reaches_server(daemon, shim):
    s = shim()
    s.initialize()
    rid = s.call("echo", {"text": "block-me"})
    reply = s.recv_for([rid])[rid]
    assert reply["result"]["isError"] is True
    assert "Blocked by MCP Monitor" in result_text(reply)
    # the server must not have run echo: count is still 0
    cid = s.call("count", {"x": "allow-me"})
    assert result_text(s.recv_for([cid])[cid]) == "0"


def test_non_intercepted_methods_pass_through_without_popup(daemon, shim):
    s = shim()
    s.initialize()
    s.send({"jsonrpc": "2.0", "id": 5, "method": "tools/list"})
    assert len(s.recv_for([5])[5]["result"]["tools"]) == 5
    s.send({"jsonrpc": "2.0", "id": 6, "method": "ping"})
    assert s.recv_for([6])[6]["result"] == {}
    assert daemon.presenter.shown == []


def test_many_concurrent_calls_get_the_right_answer_each(daemon, shim):
    s = shim()
    s.initialize()
    expected = {}
    for i in range(20):
        verdict = "allow-me" if i % 2 == 0 else "block-me"
        rid = s.call("echo", {"text": f"{verdict} #{i}"}, rpc_id=100 + i)
        expected[rid] = verdict
    got = s.recv_for(list(expected))
    assert set(got) == set(expected)
    for rid, verdict in expected.items():
        blocked = got[rid]["result"].get("isError")
        assert blocked == (verdict == "block-me"), (rid, got[rid])


def test_concurrent_shims_do_not_mix_up(daemon, shim):
    a, b = shim(), shim()
    a.initialize(), b.initialize()
    ra = a.call("echo", {"text": "allow-me A"}, rpc_id=1)
    rb = b.call("echo", {"text": "block-me B"}, rpc_id=1)   # same JSON-RPC id on purpose
    assert result_text(a.recv_for([ra])[ra]) == "echo:allow-me A"
    assert b.recv_for([rb])[rb]["result"]["isError"] is True


def test_unanswered_request_is_blocked_after_timeout(daemon, shim):
    s = shim()
    s.initialize()
    start = time.time()
    rid = s.call("echo", {"text": "ignore-me"})
    reply = s.recv_for([rid], timeout=15)[rid]
    assert reply["result"]["isError"] is True
    assert "No answer" in result_text(reply)
    assert 2.5 < time.time() - start < 8          # decision_timeout=3 in the fixture


def test_daemon_down_fails_closed(home, shim):
    write_config(home, decision_timeout=2)        # autostart disabled, no daemon running
    s = shim()
    s.initialize()
    rid = s.call("echo", {"text": "hello"})
    reply = s.recv_for([rid])[rid]
    assert reply["result"]["isError"] is True
    assert "not running" in result_text(reply)
    # non-call traffic still works while the monitor is down
    s.send({"jsonrpc": "2.0", "id": 9, "method": "ping"})
    assert s.recv_for([9])[9]["result"] == {}


def test_daemon_down_fail_open_when_configured(home, shim):
    write_config(home, decision_timeout=2, fail_mode="open")
    s = shim()
    s.initialize()
    rid = s.call("echo", {"text": "hello"})
    assert result_text(s.recv_for([rid])[rid]) == "echo:hello"


def test_disable_switch_makes_shim_a_pass_through(home, shim):
    s = shim(env_extra={"MCP_MONITOR_DISABLE": "1"})
    s.initialize()
    rid = s.call("echo", {"text": "hi"})
    assert result_text(s.recv_for([rid])[rid]) == "echo:hi"


def test_daemon_stopping_mid_request_blocks_it(daemon, shim):
    s = shim()
    s.initialize()
    rid = s.call("echo", {"text": "ignore-me"})
    time.sleep(0.5)
    daemon.stop()
    reply = s.recv_for([rid], timeout=10)[rid]
    assert reply["result"]["isError"] is True


def test_client_cancel_withdraws_pending_request(daemon, shim):
    s = shim()
    s.initialize()
    rid = s.call("echo", {"text": "ignore-me"})
    time.sleep(0.5)
    s.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
            "params": {"requestId": rid, "reason": "user stopped"}})
    time.sleep(0.5)
    # no reply is sent for a cancelled request, the popup is dismissed, server never ran it
    assert s.out.empty()
    assert daemon.broker.pending == {}
    cid = s.call("count", {"x": "allow-me"})
    assert result_text(s.recv_for([cid])[cid]) == "0"


def test_server_crash_returns_errors_for_inflight_and_exits(daemon, shim):
    s = shim()
    s.initialize()
    rid = s.call("crash", {"x": "allow-me"})
    reply = s.recv_for([rid])[rid]
    assert "error" in reply and "exited" in reply["error"]["message"]
    assert s.close() == 3          # shim exits with the server's exit code


def test_batch_requests_are_decided_individually(daemon, shim):
    s = shim()
    s.initialize()
    s.send([
        {"jsonrpc": "2.0", "id": 11, "method": "tools/call",
         "params": {"name": "echo", "arguments": {"text": "allow-me"}}},
        {"jsonrpc": "2.0", "id": 12, "method": "tools/call",
         "params": {"name": "echo", "arguments": {"text": "block-me"}}},
    ])
    got = {}
    deadline = time.time() + 10
    while len(got) < 2 and time.time() < deadline:
        msg = s.recv(5)
        for m in (msg if isinstance(msg, list) else [msg]):
            got[m["id"]] = m
    assert result_text(got[11]) == "echo:allow-me"
    assert got[12]["result"]["isError"] is True


def test_large_output_and_unicode_pass_through_intact(daemon, shim):
    s = shim()
    s.initialize()
    rid = s.call("big", {"n": 500_000, "t": "allow-me"})
    assert len(result_text(s.recv_for([rid], timeout=20)[rid])) == 500_000
    rid = s.call("echo", {"text": "allow-me héllo ✓ 日本語"})
    assert result_text(s.recv_for([rid])[rid]) == "echo:allow-me héllo ✓ 日本語"


def test_string_and_number_ids_are_preserved_in_denials(daemon, shim):
    s = shim()
    s.initialize()
    s.send({"jsonrpc": "2.0", "id": "abc-1", "method": "tools/call",
            "params": {"name": "echo", "arguments": {"text": "block-me"}}})
    reply = s.recv_for(["abc-1"])["abc-1"]
    assert reply["id"] == "abc-1" and reply["result"]["isError"] is True


def test_missing_server_command_exits_cleanly(home, shim):
    s = shim(server_cmd=[sys.executable, "-c", "import sys; sys.exit(0)"])
    assert s.close() == 0
