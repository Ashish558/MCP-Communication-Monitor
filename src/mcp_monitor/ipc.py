"""Channel between shims/gateway (clients) and the daemon.

Transport: TCP on 127.0.0.1 (random port) carrying newline-delimited JSON.
Security: the daemon writes ``daemon.json`` (port + random token) into the
per-user data directory; a client must send that token first or it is dropped.

Messages (client -> daemon)
  {"type":"hello","token":...}
  {"type":"request","ref":...,"client":...,"server":...,"method":...,"tool":...,
   "arguments_preview":...,"arguments_size":n}
  {"type":"cancel","ref":...}
  {"type":"event","event":...,...}
  {"type":"ping"}
Messages (daemon -> client)
  {"type":"hello_ok"}   {"type":"decision","ref":...,"decision":"allow|block|cancelled","reason":...}
  {"type":"pong","pending":n}
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .config import Settings, data_dir

log = logging.getLogger(__name__)

DISCOVERY_FILE = "daemon.json"
MAX_LINE = 8 * 1024 * 1024


class DaemonUnavailable(Exception):
    """The daemon could not be reached (not running, auth failed, connection lost)."""


@dataclass
class Decision:
    verdict: str   # "allow" | "block" | "cancelled"
    reason: str = ""


# --------------------------------------------------------------------------- discovery
def discovery_path(home: Path | None = None) -> Path:
    return (home or data_dir()) / DISCOVERY_FILE


def write_discovery(home: Path, port: int, token: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    path = discovery_path(home)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"port": port, "token": token, "pid": os.getpid()}), encoding="utf-8")
    os.replace(tmp, path)


def read_discovery(home: Path | None = None) -> dict:
    try:
        info = json.loads(discovery_path(home).read_text(encoding="utf-8"))
        return {"port": int(info["port"]), "token": str(info["token"]), "pid": info.get("pid")}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DaemonUnavailable(f"daemon discovery file unreadable: {exc}") from exc


def spawn_daemon() -> None:
    """Start the daemon as a detached background process (no console window)."""
    exe = sys.executable
    if os.name == "nt":
        pythonw = Path(exe).with_name("pythonw.exe")
        if pythonw.exists():
            exe = str(pythonw)
        base = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:  # leave the parent's job object if allowed, so the daemon outlives a killed shim
            subprocess.Popen([exe, "-m", "mcp_monitor", "daemon"],
                             creationflags=base | 0x01000000, **kwargs)  # CREATE_BREAKAWAY_FROM_JOB
        except OSError:
            subprocess.Popen([exe, "-m", "mcp_monitor", "daemon"], creationflags=base, **kwargs)
    else:
        subprocess.Popen([exe, "-m", "mcp_monitor", "daemon"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


# --------------------------------------------------------------------------- client
class _Slot:
    __slots__ = ("event", "decision")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.decision: Decision | None = None


class DaemonClient:
    """Blocking client used by the shim. Thread-safe; many requests can be in flight."""

    def __init__(self, settings: Settings, home: Path | None = None) -> None:
        self.settings = settings
        self.home = home or data_dir()
        self._lock = threading.RLock()
        self._sock: socket.socket | None = None
        self._pending: dict[str, _Slot] = {}

    # -- connection management
    def _connect_once(self) -> socket.socket:
        info = read_discovery(self.home)
        try:
            sock = socket.create_connection(("127.0.0.1", info["port"]), timeout=2)
            sock.settimeout(3)
            sock.sendall(_enc({"type": "hello", "token": info["token"]}))
            reply = _readline(sock)
        except OSError as exc:
            raise DaemonUnavailable(f"cannot connect to daemon: {exc}") from exc
        if not reply or json.loads(reply).get("type") != "hello_ok":
            sock.close()
            raise DaemonUnavailable("daemon rejected the handshake")
        sock.settimeout(None)
        return sock

    def _ensure_connected(self, autostart: bool = True) -> None:
        with self._lock:
            if self._sock is not None:
                return
            try:
                sock = self._connect_once()
            except DaemonUnavailable as first_error:
                if not (autostart and self.settings.autostart_daemon):
                    raise
                log.info("daemon not reachable (%s); starting it", first_error)
                try:
                    spawn_daemon()
                except OSError as exc:
                    raise DaemonUnavailable(f"could not start daemon: {exc}") from exc
                deadline = time.monotonic() + 10
                while True:
                    time.sleep(0.25)
                    try:
                        sock = self._connect_once()
                        break
                    except DaemonUnavailable:
                        if time.monotonic() > deadline:
                            raise
            self._sock = sock
            threading.Thread(target=self._reader, args=(sock,), name="daemon-reader", daemon=True).start()

    def _reader(self, sock: socket.socket) -> None:
        try:
            f = sock.makefile("rb")
            for line in f:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("type") == "decision":
                    with self._lock:
                        slot = self._pending.get(msg.get("ref"))
                    if slot:
                        slot.decision = Decision(str(msg.get("decision", "block")), str(msg.get("reason", "")))
                        slot.event.set()
        except OSError:
            pass
        finally:
            self._drop(sock)

    def _drop(self, sock: socket.socket) -> None:
        """Connection lost: every waiting request is blocked (fail closed)."""
        with self._lock:
            if self._sock is sock:
                self._sock = None
            for slot in self._pending.values():
                if not slot.event.is_set():
                    slot.decision = Decision("block", "the monitor connection was lost")
                    slot.event.set()
        try:
            sock.close()
        except OSError:
            pass

    def _send(self, msg: dict) -> None:
        with self._lock:
            sock = self._sock
            if sock is None:
                raise DaemonUnavailable("not connected")
            try:
                sock.sendall(_enc(msg))
            except OSError as exc:
                self._drop(sock)
                raise DaemonUnavailable(f"send failed: {exc}") from exc

    # -- public API
    def warm_up(self) -> None:
        """Connect (starting the daemon if needed) ahead of the first request."""
        try:
            self._ensure_connected()
        except DaemonUnavailable as exc:
            log.warning("warm-up failed: %s", exc)

    def decide(self, ref: str, info: dict, timeout: float) -> Decision:
        """Send a request and wait for the user's decision. Raises DaemonUnavailable."""
        slot = _Slot()
        with self._lock:
            self._ensure_connected()
            self._pending[ref] = slot
        try:
            self._send({"type": "request", "ref": ref, **info})
            if not slot.event.wait(timeout):
                self.cancel(ref)
                return Decision("block", "no answer from the monitor")
            assert slot.decision is not None
            return slot.decision
        finally:
            with self._lock:
                self._pending.pop(ref, None)

    def cancel(self, ref: str) -> None:
        try:
            self._send({"type": "cancel", "ref": ref})
        except DaemonUnavailable:
            pass

    def event(self, payload: dict) -> None:
        """Best-effort informational event (never autostarts, never raises)."""
        try:
            self._ensure_connected(autostart=False)
            self._send({"type": "event", **payload})
        except DaemonUnavailable:
            pass

    def close(self) -> None:
        with self._lock:
            sock = self._sock
        if sock:
            self._drop(sock)


def ping(home: Path | None = None, timeout: float = 2.0) -> dict | None:
    """Return the daemon's pong (``{"pending": n}``) or ``None`` if it is not running."""
    try:
        info = read_discovery(home)
        with socket.create_connection(("127.0.0.1", info["port"]), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(_enc({"type": "hello", "token": info["token"]}))
            if json.loads(_readline(sock) or b"{}").get("type") != "hello_ok":
                return None
            sock.sendall(_enc({"type": "ping"}))
            return json.loads(_readline(sock) or b"{}")
    except (DaemonUnavailable, OSError, ValueError):
        return None


def new_ref() -> str:
    return uuid.uuid4().hex


def _enc(msg: dict) -> bytes:
    return json.dumps(msg, ensure_ascii=False).encode("utf-8") + b"\n"


def _readline(sock: socket.socket) -> bytes:
    buf = bytearray()
    while not buf.endswith(b"\n"):
        chunk = sock.recv(1)
        if not chunk:
            break
        buf += chunk
    return bytes(buf)
