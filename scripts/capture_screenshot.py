"""Renders one approval popup with sample data and saves a clean screenshot of just that window.

Run from the repo root:  .venv\\Scripts\\python scripts\\capture_screenshot.py
Output: docs/images/popup.png   (sample data only, nothing real is shown)
"""

import ctypes
import sys
import time
import tkinter as tk
from pathlib import Path

from PIL import ImageGrab

from mcp_monitor.config import Settings
from mcp_monitor.daemon.broker import PendingRequest
from mcp_monitor.daemon.ui import TkPresenter, _Card

OUT = Path(__file__).resolve().parents[1] / "docs" / "images" / "popup.png"
OUT.parent.mkdir(parents=True, exist_ok=True)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # make window coordinates match screen pixels
except Exception:
    pass


class _NoBroker:
    def resolve(self, *args):
        pass


presenter = TkPresenter(Settings(), use_tray=False)
presenter.broker = _NoBroker()
presenter.root = tk.Tk()
presenter.root.withdraw()

now = time.time()
req = PendingRequest(
    id=1, ref="demo", client="claude-desktop (claude-ai 0.1.0)", server="filesystem", method="tools/call",
    tool="write_file",
    arguments_preview='{\n  "path": "C:/demo/notes.txt",\n  "content": "Meeting notes: ship the monitor on Friday.",\n'
                      '  "api_key": "***"\n}',
    arguments_size=120, created=now, deadline=now + 57)
card = _Card(presenter, req, 0)


def grab() -> None:
    card.tick(0)
    card.win.update_idletasks()
    hwnd = ctypes.windll.user32.GetParent(card.win.winfo_id()) or card.win.winfo_id()
    rect = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    # Windows 11 adds an invisible ~8px resize border + shadow; crop it so nothing behind shows through.
    ImageGrab.grab(bbox=(rect.left + 9, rect.top, rect.right - 9, rect.bottom - 9), all_screens=True).save(OUT)
    print("saved", OUT)
    presenter.root.quit()


import ctypes.wintypes  # noqa: E402  (needed for RECT)

presenter.root.after(1500, grab)
presenter.root.mainloop()
sys.exit(0)
