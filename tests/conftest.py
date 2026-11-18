"""Shared fixtures: an in-process daemon with a rule-driven presenter, and a shim runner."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from mcp_monitor.config import Settings
from mcp_monitor.daemon.runtime import Daemon
from mcp_monitor.daemon.scripted import ScriptedPresenter

FAKE_SERVER = str(Path(__file__).with_name("fake_mcp_server.py"))


def verdict_rule(req):
    """Decide by what the test put in the arguments: 'allow-me', 'block-me', or silence."""
    if "allow-me" in req.arguments_preview:
        return ("allow", "test allow")
    if "block-me" in req.arguments_preview:
        return ("block", "test block")
    if "ignore-me" in req.arguments_preview:
        return None
    return ("allow", "default allow")


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_MONITOR_HOME", str(tmp_path))
    monkeypatch.delenv("MCP_MONITOR_DISABLE", raising=False)
    monkeypatch.delenv("MCP_MONITOR_FAIL_MODE", raising=False)
    return tmp_path


def write_config(home: Path, **values) -> None:
    values.setdefault("autostart_daemon", False)
    (home / "config.json").write_text(json.dumps(values), encoding="utf-8")


@pytest.fixture()
def daemon(home):
    """Daemon whose presenter answers by rule; stopped at the end of the test."""
    write_config(home, decision_timeout=3)
    presenter = ScriptedPresenter(verdict_rule, delay=0.05)
    d = Daemon(presenter, Settings.load(home), home)
    d.start()
    d.presenter = presenter
    yield d
    d.stop()


class ShimProc:
    """Runs ``mcp_monitor shim -- python fake_mcp_server.py`` and talks JSON-RPC to it."""

    def __init__(self, home: Path, server_cmd=None, env_extra=None):
        env = {**os.environ, "MCP_MONITOR_HOME": str(home), **(env_extra or {})}
        cmd = [sys.executable, "-m", "mcp_monitor", "shim", "--server-name", "fake",
               "--client", "pytest", "--", *(server_cmd or [sys.executable, FAKE_SERVER])]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env=env)
        self.out: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self._next = 0

    def _read(self):
        for line in self.proc.stdout:
            try:
                self.out.put(json.loads(line))
            except ValueError:
                self.out.put({"_raw": line})
        self.out.put(None)

    def send(self, msg) -> None:
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()

    def call(self, name: str, arguments=None, rpc_id=None, method="tools/call") -> int:
        self._next += 1
        rpc_id = rpc_id if rpc_id is not None else self._next
        self.send({"jsonrpc": "2.0", "id": rpc_id, "method": method,
                   "params": {"name": name, "arguments": arguments or {}}})
        return rpc_id

    def recv(self, timeout: float = 10):
        return self.out.get(timeout=timeout)

    def recv_for(self, ids, timeout: float = 15) -> dict:
        """Collect responses until every id in ``ids`` has answered."""
        wanted, got = set(ids), {}
        deadline = time.time() + timeout
        while wanted - set(got) and time.time() < deadline:
            msg = self.recv(max(0.1, deadline - time.time()))
            if msg and "id" in msg:
                got[msg["id"]] = msg
        return got

    def initialize(self) -> dict:
        self.send({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "pytest-client", "version": "1"}}})
        return self.recv_for([0])[0]

    def close(self) -> int:
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            return self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return -1


@pytest.fixture()
def shim(home):
    made: list[ShimProc] = []

    def make(**kw) -> ShimProc:
        s = ShimProc(home, **kw)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


def result_text(msg: dict) -> str:
    return msg["result"]["content"][0]["text"]
