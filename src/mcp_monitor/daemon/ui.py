"""Tkinter approval popups.

Design:
* The Tk main loop owns the main thread; the broker runs on another thread and
  talks to the UI through a thread-safe queue (``show`` / ``close``).
* Each pending request gets its own always-on-top window. At most
  ``max_visible_popups`` are open at once; the rest wait in a queue and open as
  earlier ones are answered.
* Safe-by-default input: Allow/Block are inert for a short moment after a
  popup appears (so a keystroke aimed at another window cannot approve it),
  Enter does nothing, Esc and the window's X mean Block.
"""

from __future__ import annotations

import collections
import logging
import os
import queue
import time

from ..config import Settings, data_dir
from .broker import Broker, PendingRequest

log = logging.getLogger(__name__)

BG = "#1f2430"
FG = "#e6e9ef"
MUTED = "#9aa3b2"
ALLOW = "#2e9f5b"
BLOCK = "#c0392b"


def bring_to_front(window) -> None:
    """Best effort to appear above other apps on Windows (topmost flag does most of the work)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
        foreground = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(foreground, None)
        my_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        if fg_thread and fg_thread != my_thread:
            user32.AttachThreadInput(my_thread, fg_thread, True)
            user32.SetForegroundWindow(hwnd)
            user32.AttachThreadInput(my_thread, fg_thread, False)
        else:
            user32.SetForegroundWindow(hwnd)

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND), ("dwFlags", wintypes.DWORD),
                        ("uCount", wintypes.UINT), ("dwTimeout", wintypes.DWORD)]

        info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0x3 | 0xC, 5, 0)  # tray + caption, until focus
        user32.FlashWindowEx(ctypes.byref(info))
    except Exception:
        log.debug("bring_to_front failed", exc_info=True)


class _Card:
    """One approval window."""

    def __init__(self, ui: "TkPresenter", req: PendingRequest, index: int) -> None:
        import tkinter as tk

        self.ui, self.req, self.done = ui, req, False
        win = self.win = tk.Toplevel(ui.root)
        win.title("MCP Monitor - approval required")
        win.configure(bg=BG)
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", lambda: self._decide("block", "The popup was closed."))
        win.bind("<Escape>", lambda _e: self._decide("block", "Blocked with Esc."))

        width, height = 600, 470
        x = max(0, (win.winfo_screenwidth() - width) // 2 + 30 * index)
        y = max(0, (win.winfo_screenheight() - height) // 2 + 30 * index)
        win.geometry(f"{width}x{height}+{x}+{y}")
        win.minsize(480, 380)

        tk.Label(win, text="An MCP request is waiting for your decision", bg="#d68910", fg="white",
                 font=("Segoe UI", 12, "bold"), anchor="w", padx=14, pady=8).pack(fill="x")

        grid = tk.Frame(win, bg=BG, padx=14, pady=10)
        grid.pack(fill="x")
        rows = [("From", req.client), ("MCP server", req.server), ("Action", req.method),
                ("Tool", req.tool), ("Requested", time.strftime("%H:%M:%S", time.localtime(req.created)))]
        for r, (label, value) in enumerate(rows):
            tk.Label(grid, text=label, bg=BG, fg=MUTED, font=("Segoe UI", 10), anchor="w").grid(
                row=r, column=0, sticky="w", padx=(0, 14))
            tk.Label(grid, text=value, bg=BG, fg=FG, font=("Segoe UI", 10, "bold"), anchor="w",
                     wraplength=440, justify="left").grid(row=r, column=1, sticky="w")

        tk.Label(win, text="Parameters (secrets are masked)", bg=BG, fg=MUTED, font=("Segoe UI", 9),
                 anchor="w").pack(fill="x", padx=14)
        box = tk.Frame(win, bg=BG, padx=14)
        box.pack(fill="both", expand=True, pady=(2, 6))
        text = tk.Text(box, height=8, wrap="word", bg="#141821", fg=FG, relief="flat",
                       font=("Consolas", 9), padx=8, pady=6)
        scroll = tk.Scrollbar(box, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        text.insert("1.0", req.arguments_preview or "(no parameters)")
        text.configure(state="disabled")

        self.timer = tk.Label(win, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor="w")
        self.timer.pack(fill="x", padx=14)
        self.more = tk.Label(win, text="", bg=BG, fg="#d68910", font=("Segoe UI", 9), anchor="w")
        self.more.pack(fill="x", padx=14)

        buttons = tk.Frame(win, bg=BG, padx=14, pady=10)
        buttons.pack(fill="x")
        common = dict(font=("Segoe UI", 11, "bold"), fg="white", relief="flat", width=12, pady=6,
                      disabledforeground="#cccccc", cursor="hand2")
        self.block_btn = tk.Button(buttons, text="Block", bg=BLOCK, activebackground="#992d22",
                                   command=lambda: self._decide("block", "Blocked by the user."),
                                   state="disabled", **common)
        self.allow_btn = tk.Button(buttons, text="Allow", bg=ALLOW, activebackground="#23794a",
                                   command=lambda: self._decide("allow", "Allowed by the user."),
                                   state="disabled", **common)
        self.block_btn.pack(side="left")
        self.allow_btn.pack(side="right")
        win.after(ui.settings.button_delay_ms, self._enable)
        win.after(40, lambda: (win.lift(), win.focus_force(), bring_to_front(win)))

    def _enable(self) -> None:
        if not self.done:
            self.block_btn.configure(state="normal")
            self.allow_btn.configure(state="normal")

    def _decide(self, verdict: str, reason: str) -> None:
        if self.done:
            return
        self.done = True
        self.ui.broker.resolve(self.req.id, verdict, reason)
        self.destroy()

    def tick(self, waiting: int) -> None:
        remaining = max(0, int(self.req.deadline - time.time()))
        self.timer.configure(text=f"Blocked automatically in {remaining}s if there is no answer.")
        self.more.configure(text=f"{waiting} more request(s) waiting after this one." if waiting else "")

    def destroy(self) -> None:
        self.done = True
        try:
            self.win.destroy()
        except Exception:
            pass


class TkPresenter:
    def __init__(self, settings: Settings | None = None, use_tray: bool = True) -> None:
        self.settings = settings or Settings.load()
        self.use_tray = use_tray
        self.broker: Broker = None  # type: ignore[assignment]
        self.root = None
        self.cards: dict[int, _Card] = {}
        self.waiting: collections.deque[PendingRequest] = collections.deque()
        self._q: queue.Queue = queue.Queue()
        self._count = 0
        self._tray = None

    # -- Presenter protocol (called from the broker thread) ------------------
    def attach(self, broker: Broker) -> None:
        self.broker = broker

    def show(self, req: PendingRequest) -> None:
        self._q.put(("show", req))

    def close(self, req_id: int, outcome: str) -> None:
        self._q.put(("close", req_id))

    def pending_changed(self, count: int) -> None:
        self._count = count

    def stop(self) -> None:
        self._q.put(("stop", None))

    # -- main thread --------------------------------------------------------
    def run(self) -> None:
        import tkinter as tk

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.after(80, self._poll)
        if self.use_tray:
            from .tray import start_tray
            self._tray = start_tray(self.stop, lambda: self._count, data_dir())
        try:
            self.root.mainloop()
        finally:
            if self._tray is not None:
                try:
                    self._tray.stop()
                except Exception:
                    pass

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "show":
                    self.waiting.append(payload)
                elif kind == "close":
                    card = self.cards.pop(payload, None)
                    if card:
                        card.destroy()
                    self.waiting = collections.deque(r for r in self.waiting if r.id != payload)
                elif kind == "stop":
                    for card in list(self.cards.values()):
                        card.destroy()
                    self.root.quit()
                    return
        except queue.Empty:
            pass
        for req_id in [i for i, c in self.cards.items() if c.done]:
            self.cards.pop(req_id, None)
        while self.waiting and len(self.cards) < self.settings.max_visible_popups:
            req = self.waiting.popleft()
            try:
                self.cards[req.id] = _Card(self, req, len(self.cards))
            except Exception:
                log.exception("could not open popup")
                self.broker.resolve(req.id, "block", "The approval popup could not be shown.")
        for card in self.cards.values():
            card.tick(len(self.waiting))
        self.root.after(150, self._poll)
