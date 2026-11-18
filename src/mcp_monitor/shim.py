"""Stdio shim: a transparent proxy in front of one MCP server.

Claude starts the shim exactly as it would start the real server. The shim
starts the real server as a child process and relays JSON-RPC lines in both
directions. Requests whose method is in ``Settings.intercept_methods``
(``tools/call`` by default) are *held* until the daemon says Allow or Block:

* Allow  -> the original line is forwarded to the real server untouched.
* Block  -> the real server never sees it; the shim answers Claude itself.

Everything else (initialize, tools/list, notifications, responses) passes
through unchanged, so the shim does not depend on the MCP protocol version.

Rules this module keeps:
* stdout carries protocol messages only (all logging goes to a file);
* any failure while deciding blocks the call (fail closed) unless the user
  explicitly configured ``fail_mode = "open"``;
* the child's stderr is inherited, so Claude's server logs still work.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import shutil
import subprocess
import sys
import threading
from typing import BinaryIO, Callable

from . import protocol
from .config import Settings, data_dir
from .ipc import DaemonClient, DaemonUnavailable, Decision, new_ref
from .jobobject import KillOnCloseJob

log = logging.getLogger("mcp_monitor.shim")


class Shim:
    def __init__(
        self,
        command: list[str],
        server_name: str,
        client_name: str,
        settings: Settings,
        client: DaemonClient | None = None,
        stdin: BinaryIO | None = None,
        stdout: BinaryIO | None = None,
        exit_fn: Callable[[int], None] = os._exit,
    ) -> None:
        self.command = command
        self.server_name = server_name
        self.client_name = client_name
        self.settings = settings
        self.client = client or DaemonClient(settings)
        self._stdin = stdin or sys.stdin.buffer
        self._stdout = stdout or sys.stdout.buffer
        self._exit_fn = exit_fn
        self._intercept = frozenset() if settings.disabled else frozenset(settings.intercept_methods)

        self._child: subprocess.Popen | None = None
        self._job = KillOnCloseJob()
        self._out_lock = threading.Lock()      # serialises writes to our stdout
        self._child_lock = threading.Lock()    # serialises writes to the child's stdin
        self._state_lock = threading.Lock()
        self._held: dict[str, tuple[str, threading.Event]] = {}   # id_key -> (daemon ref, cancelled)
        self._inflight: dict[str, object] = {}                    # id_key -> original id (sent to child)
        self._child_dead = False
        self._exited = False

    # ------------------------------------------------------------------ lifecycle
    def run(self) -> int:
        self._start_child()
        threading.Thread(target=self._pump_child_output, name="child-out", daemon=True).start()
        try:
            for line in iter(self._stdin.readline, b""):
                self._on_client_line(line)
        except (OSError, ValueError):
            log.info("client stdin closed with an error", exc_info=True)
        return self._on_client_eof()

    def _start_child(self) -> None:
        exe = shutil.which(self.command[0]) or self.command[0]
        argv = [exe, *self.command[1:]]
        log.info("starting server %r as %s", self.server_name, argv)
        try:
            self._child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
        except OSError as exc:
            log.error("cannot start %s: %s", argv, exc)
            sys.stderr.write(f"mcp-monitor: cannot start MCP server {argv[0]!r}: {exc}\n")
            sys.stderr.flush()
            self._exit_fn(127)
            raise
        self._job.add(self._child)

    def _on_client_eof(self) -> int:
        """Claude closed our stdin: shut the real server down cleanly."""
        child = self._child
        assert child is not None
        try:
            child.stdin.close()
        except OSError:
            pass
        try:
            return child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.terminate()
            try:
                return child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                return child.wait()
        finally:
            self.client.close()

    # ------------------------------------------------------------------ child -> client
    def _pump_child_output(self) -> None:
        child = self._child
        assert child is not None
        try:
            for line in iter(child.stdout.readline, b""):
                if b'"id"' in line:
                    self._note_response(line)
                self._write_client(line if line.endswith(b"\n") else line + b"\n")
        except (OSError, ValueError):
            log.info("child output pipe closed", exc_info=True)
        rc = child.wait()
        log.info("server %r exited with code %s", self.server_name, rc)
        self._child_dead = True
        self._fail_inflight(f"MCP server {self.server_name!r} exited unexpectedly (code {rc}).")
        self._finish(rc)

    def _note_response(self, line: bytes) -> None:
        msg = protocol.parse_line(line)
        items = msg if isinstance(msg, list) else [msg]
        with self._state_lock:
            for item in items:
                if protocol.is_response(item):
                    self._inflight.pop(protocol.id_key(item["id"]), None)

    def _fail_inflight(self, message: str) -> None:
        with self._state_lock:
            ids = list(self._inflight.values())
            self._inflight.clear()
        for rpc_id in ids:
            self._write_client(protocol.encode(
                protocol.error_response(rpc_id, protocol.INTERNAL_ERROR_CODE, message)))

    def _finish(self, rc: int) -> None:
        with self._state_lock:
            if self._exited:
                return
            self._exited = True
        try:
            self._stdout.flush()
        except (OSError, ValueError):
            pass
        self.client.close()
        self._exit_fn(rc)

    # ------------------------------------------------------------------ client -> child
    def _on_client_line(self, line: bytes) -> None:
        if not line.strip():
            return
        msg = protocol.parse_line(line)
        if msg is None:
            # Not valid JSON: cannot be recognised as a call, so it is passed through
            # (the real server will reject it). Logged for the audit trail.
            log.warning("non-JSON line from client passed through (%d bytes)", len(line))
            self._forward(line)
        elif isinstance(msg, list):
            threading.Thread(target=self._handle_batch, args=(msg, line), daemon=True).start()
        elif isinstance(msg, dict):
            self._handle_message(msg, line)
        else:
            self._forward(line)

    def _handle_message(self, msg: dict, line: bytes) -> None:
        method = msg.get("method")
        if method == "initialize" and protocol.is_request(msg):
            name = protocol.client_name_from_initialize(msg)
            if name:
                # keep the configured label and add what the client says about itself
                self.client_name = name if self.client_name in ("", "unknown") else f"{self.client_name} ({name})"
            threading.Thread(target=self._announce, args=(name,), daemon=True).start()
        elif method == "notifications/cancelled" and self._cancel_held(msg):
            return  # the request never reached the server, so nothing to cancel there
        if protocol.is_request(msg) and method in self._intercept:
            threading.Thread(target=self._hold, args=(msg, line), name="held-call", daemon=True).start()
        else:
            self._forward(line, msg)

    def _announce(self, reported_client: str | None) -> None:
        self.client.warm_up() if not self.settings.disabled else None
        self.client.event({"event": "session_start", "client": reported_client or self.client_name,
                           "server": self.server_name})

    def _cancel_held(self, msg: dict) -> bool:
        params = msg.get("params")
        if not isinstance(params, dict) or params.get("requestId") is None:
            return False
        key = protocol.id_key(params["requestId"])
        with self._state_lock:
            entry = self._held.get(key)
        if not entry:
            return False
        ref, cancelled = entry
        cancelled.set()
        self.client.cancel(ref)
        return True

    def _hold(self, msg: dict, line: bytes) -> None:
        """Wait for the decision on one request, then forward or deny it."""
        key = protocol.id_key(msg["id"])
        cancelled = threading.Event()
        ref = new_ref()
        with self._state_lock:
            self._held[key] = (ref, cancelled)
        try:
            decision = self._ask(ref, msg)
        except Exception as exc:  # anything unexpected must not let the call through
            log.exception("decision failed")
            decision = Decision("block", f"internal monitor error ({exc.__class__.__name__})")
        finally:
            with self._state_lock:
                self._held.pop(key, None)
        if cancelled.is_set() or decision.verdict == "cancelled":
            log.info("request %s cancelled by client while pending", key)
            return  # the client withdrew it; MCP expects no reply
        if decision.verdict == "allow":
            self._forward(line, msg)
        else:
            self._write_client(protocol.encode(protocol.denial_response(msg, decision.reason)))

    def _ask(self, ref: str, msg: dict) -> Decision:
        call = protocol.describe(msg)
        text, size = protocol.preview(call.arguments, self.settings.preview_max_chars)
        info = {
            "client": protocol.sanitize_text(self.client_name),
            "server": protocol.sanitize_text(self.server_name),
            "method": call.method,
            "tool": protocol.sanitize_text(call.name),
            "arguments_preview": text,
            "arguments_size": size,
        }
        try:
            return self.client.decide(ref, info, timeout=self.settings.decision_timeout + 5)
        except DaemonUnavailable as exc:
            if self.settings.fail_mode == "open":
                log.warning("monitor unavailable (%s): allowing because fail_mode=open", exc)
                return Decision("allow", "monitor unavailable (fail-open)")
            log.warning("monitor unavailable (%s): blocking", exc)
            return Decision("block", "The monitor is not running, so the request was blocked for safety.")

    def _handle_batch(self, items: list, line: bytes) -> None:
        """JSON-RPC batch: decide each intercepted item, forward the rest as one batch."""
        results: dict[int, Decision] = {}
        threads = []

        def decide(index: int, item: dict) -> None:
            ref = new_ref()
            key = protocol.id_key(item["id"])
            with self._state_lock:
                self._held[key] = (ref, threading.Event())
            try:
                results[index] = self._ask(ref, item)
            except Exception:
                log.exception("batch decision failed")
                results[index] = Decision("block", "internal monitor error")
            finally:
                with self._state_lock:
                    self._held.pop(key, None)

        for i, item in enumerate(items):
            if isinstance(item, dict) and protocol.is_request(item) and item.get("method") in self._intercept:
                t = threading.Thread(target=decide, args=(i, item), daemon=True)
                t.start()
                threads.append(t)
        for t in threads:
            t.join()

        forward = []
        for i, item in enumerate(items):
            decision = results.get(i)
            if decision is None or decision.verdict == "allow":
                forward.append(item)
            elif decision.verdict == "block":
                self._write_client(protocol.encode(protocol.denial_response(item, decision.reason)))
        if forward:
            self._forward(protocol.encode(forward), forward)

    def _forward(self, line: bytes, msg=None) -> None:
        """Write one line to the real server; if that is impossible, answer with an error."""
        if msg is None:
            msg = protocol.parse_line(line)
        requests = [m for m in (msg if isinstance(msg, list) else [msg]) if protocol.is_request(m)]
        with self._state_lock:
            for r in requests:
                self._inflight[protocol.id_key(r["id"])] = r["id"]
        try:
            with self._child_lock:
                assert self._child is not None and self._child.stdin is not None
                self._child.stdin.write(line if line.endswith(b"\n") else line + b"\n")
                self._child.stdin.flush()
        except (OSError, ValueError):
            log.warning("could not write to server %r (it has exited)", self.server_name)
            with self._state_lock:
                for r in requests:
                    self._inflight.pop(protocol.id_key(r["id"]), None)
            for r in requests:
                self._write_client(protocol.encode(protocol.error_response(
                    r["id"], protocol.INTERNAL_ERROR_CODE, f"MCP server {self.server_name!r} is not running.")))

    def _write_client(self, data: bytes) -> None:
        try:
            with self._out_lock:
                self._stdout.write(data)
                self._stdout.flush()
        except (OSError, ValueError):
            log.info("client is gone; shutting down")
            self._finish(0)


def configure_logging(level: str) -> None:
    """Log to a file only: stdout is reserved for protocol messages."""
    home = data_dir()
    try:
        home.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            home / "shim.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    except OSError:
        handler = logging.NullHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(process)d %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def run_shim(server_name: str, client_name: str, command: list[str]) -> int:
    settings = Settings.load()
    configure_logging(settings.log_level)
    log.info("shim for %r (client=%s, disabled=%s)", server_name, client_name, settings.disabled)
    return Shim(command, server_name, client_name, settings).run()
