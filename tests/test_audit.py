"""Audit log: what is written, and the privacy switch."""

from __future__ import annotations

import json

from conftest import verdict_rule, write_config
from mcp_monitor.config import Settings
from mcp_monitor.daemon.runtime import Daemon
from mcp_monitor.daemon.scripted import ScriptedPresenter
from mcp_monitor.ipc import DaemonClient


def _ask(home, text):
    settings = Settings.load(home)
    daemon = Daemon(ScriptedPresenter(verdict_rule, delay=0.05), settings, home)
    daemon.start()
    try:
        client = DaemonClient(settings, home)
        decision = client.decide("ref-1", {"client": "t", "server": "s", "method": "tools/call", "tool": "echo",
                                           "arguments_preview": text, "arguments_size": len(text)}, timeout=10)
        client.close()
    finally:
        daemon.stop()
    entries = [json.loads(line) for line in (home / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    return decision, entries


def test_every_request_and_decision_is_logged_with_parameters_by_default(home):
    write_config(home, decision_timeout=3)
    decision, entries = _ask(home, "allow-me marker-123")
    assert decision.verdict == "allow"
    requested = next(e for e in entries if e["event"] == "requested")
    decided = next(e for e in entries if e["event"] == "decided")
    assert "marker-123" in requested["arguments"] and decided["decision"] == "allow"


def test_log_arguments_off_keeps_decisions_but_never_writes_parameters(home):
    write_config(home, decision_timeout=3, log_arguments=False)
    decision, entries = _ask(home, "allow-me private-marker-456")
    assert decision.verdict == "allow"
    assert any(e["event"] == "decided" for e in entries)
    assert "private-marker-456" not in (home / "audit.jsonl").read_text(encoding="utf-8")
    assert all("arguments" not in e for e in entries)
