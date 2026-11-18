"""Broker limits that the end-to-end tests do not reach."""

from __future__ import annotations

import threading
import time

from conftest import verdict_rule, write_config
from mcp_monitor.config import Settings
from mcp_monitor.daemon.runtime import Daemon
from mcp_monitor.daemon.scripted import ScriptedPresenter
from mcp_monitor.ipc import DaemonClient


def test_requests_beyond_max_pending_are_blocked_immediately(home):
    write_config(home, decision_timeout=6, max_pending=2)
    settings = Settings.load(home)
    daemon = Daemon(ScriptedPresenter(verdict_rule), settings, home)
    daemon.start()
    client = DaemonClient(settings, home)
    info = {"client": "t", "server": "s", "method": "tools/call", "tool": "echo",
            "arguments_preview": "ignore-me", "arguments_size": 9}
    waiting = [threading.Thread(target=client.decide, args=(f"r{i}", info, 10), daemon=True) for i in range(2)]
    try:
        for t in waiting:
            t.start()
        deadline = time.time() + 5
        while len(daemon.broker.pending) < 2 and time.time() < deadline:
            time.sleep(0.05)
        started = time.time()
        extra = client.decide("r-extra", info, timeout=10)       # the third one
        assert extra.verdict == "block" and "Too many" in extra.reason
        assert time.time() - started < 2                        # refused at once, not after the timeout
    finally:
        client.close()
        daemon.stop()
