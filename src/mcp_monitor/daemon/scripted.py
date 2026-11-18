"""Headless presenter that decides by rule instead of showing a popup.

Used by the tests and by ``mcp-monitor daemon --auto allow|block`` for demos
and CI. It is never the default: a real run always shows the popup.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from .broker import Broker, PendingRequest

Rule = Callable[[PendingRequest], Optional[tuple]]


class ScriptedPresenter:
    """``rule(req)`` returns ``(verdict, reason)``, or ``None`` to never answer."""

    def __init__(self, rule: Rule, delay: float = 0.0) -> None:
        self.rule = rule
        self.delay = delay
        self.broker: Broker | None = None
        self.shown: list[PendingRequest] = []
        self.closed: list[tuple] = []
        self._stop = threading.Event()

    def attach(self, broker: Broker) -> None:
        self.broker = broker

    def show(self, req: PendingRequest) -> None:
        self.shown.append(req)
        answer = self.rule(req)
        if answer is not None:
            assert self.broker is not None
            timer = threading.Timer(self.delay, self.broker.resolve, (req.id, *answer))
            timer.daemon = True
            timer.start()

    def close(self, req_id: int, outcome: str) -> None:
        self.closed.append((req_id, outcome))

    def pending_changed(self, count: int) -> None:
        pass

    def run(self) -> None:
        self._stop.wait()

    def stop(self) -> None:
        self._stop.set()
