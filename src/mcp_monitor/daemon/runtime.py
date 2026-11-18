"""Daemon lifecycle: single-instance lock, asyncio thread, discovery file."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import threading
from pathlib import Path

from ..config import Settings, data_dir
from ..ipc import discovery_path, write_discovery
from .audit import AuditLog
from .broker import Broker, Presenter

log = logging.getLogger(__name__)


class AlreadyRunning(Exception):
    pass


class SingleInstance:
    """OS-level lock held for the daemon's lifetime (released automatically if it crashes)."""

    def __init__(self, home: Path) -> None:
        home.mkdir(parents=True, exist_ok=True)
        self._path = home / "daemon.lock"
        self._fh = open(self._path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._fh.close()
            raise AlreadyRunning("another MCP Monitor daemon is already running") from exc

    def release(self) -> None:
        try:
            if os.name == "nt":
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            self._fh.close()
        except OSError:
            pass


class Daemon:
    def __init__(self, presenter: Presenter, settings: Settings | None = None, home: Path | None = None) -> None:
        self.home = home or data_dir()
        self.settings = settings or Settings.load(self.home)
        self.presenter = presenter
        self.audit = AuditLog(self.home / "audit.jsonl")
        self.token = secrets.token_urlsafe(32)
        self.broker = Broker(presenter, self.audit, self.settings, self.token)
        presenter.attach(self.broker)
        self.port = 0
        self._lock: SingleInstance | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopped = False

    def start(self) -> int:
        """Start serving in a background thread. Returns the TCP port."""
        self._lock = SingleInstance(self.home)
        ready = threading.Event()
        failure: list[BaseException] = []

        def run() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self.port = self._loop.run_until_complete(self.broker.start())
            except BaseException as exc:  # noqa: BLE001 - reported to the starter
                failure.append(exc)
                ready.set()
                return
            ready.set()
            self._loop.run_forever()

        self._thread = threading.Thread(target=run, name="broker-loop", daemon=True)
        self._thread.start()
        ready.wait(10)
        if failure or not self.port:
            self._lock.release()
            raise RuntimeError(f"daemon failed to start: {failure[0] if failure else 'timeout'}")
        write_discovery(self.home, self.port, self.token)
        self.audit.write({"event": "daemon_started", "port": self.port, "pid": os.getpid()})
        log.info("daemon listening on 127.0.0.1:%s", self.port)
        return self.port

    def stop(self) -> None:
        """Block all pending requests, close sockets, remove the discovery file. Idempotent."""
        if self._stopped:
            return
        self._stopped = True
        if self._loop and self._loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(self.broker.shutdown(), self._loop).result(5)
            except Exception:
                log.exception("shutdown error")
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread:
                self._thread.join(3)
        try:
            path = discovery_path(self.home)
            if path.exists():
                path.unlink()
        except OSError:
            pass
        self.audit.write({"event": "daemon_stopped"})
        self.audit.close()
        if self._lock:
            self._lock.release()

    def serve_forever(self) -> None:
        self.start()
        try:
            self.presenter.run()  # type: ignore[attr-defined]
        finally:
            self.stop()
