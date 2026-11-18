"""Optional system-tray icon (needs ``pip install mcp-monitor[tray]``)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)


def start_tray(on_quit: Callable[[], None], get_pending: Callable[[], int], home: Path):
    """Start the tray icon in its own thread. Returns the icon, or ``None`` if unavailable."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        log.info("pystray/Pillow not installed: running without a tray icon")
        return None
    try:
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((4, 4, 60, 60), fill=(214, 137, 16, 255))
        draw.rectangle((26, 16, 38, 38), fill="white")
        draw.ellipse((26, 43, 38, 55), fill="white")

        def open_logs() -> None:
            if os.name == "nt":
                os.startfile(str(home))  # noqa: S606 - opens a folder in Explorer

        menu = pystray.Menu(
            pystray.MenuItem(lambda _i: f"MCP Monitor - {get_pending()} pending", None, enabled=False),
            pystray.MenuItem("Open log folder", lambda *_: open_logs()),
            pystray.MenuItem("Quit (blocks pending requests)", lambda *_: on_quit()),
        )
        icon = pystray.Icon("mcp-monitor", image, "MCP Monitor", menu)
        icon.run_detached()
        return icon
    except Exception:
        log.exception("tray icon failed to start")
        return None
