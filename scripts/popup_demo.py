"""Manual demo: shows real popups for three concurrent fake tool calls.

Run from the repo root:   .venv\\Scripts\\python scripts\\popup_demo.py
Uses an isolated data dir, so it never touches your real MCP Monitor state.
Click Allow / Block in each popup and watch the printed results.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

home = tempfile.mkdtemp(prefix="mcpmon-demo-")
os.environ["MCP_MONITOR_HOME"] = home
Path(home, "config.json").write_text(json.dumps({"decision_timeout": 90, "autostart_daemon": False}))

from mcp_monitor.config import Settings                     # noqa: E402
from mcp_monitor.daemon.runtime import Daemon               # noqa: E402
from mcp_monitor.daemon.ui import TkPresenter               # noqa: E402

fake = str(Path(__file__).resolve().parents[1] / "tests" / "fake_mcp_server.py")
presenter = TkPresenter(Settings.load(Path(home)), use_tray=False)
daemon = Daemon(presenter, Settings.load(Path(home)), Path(home))
daemon.start()


def client() -> None:
    proc = subprocess.Popen([sys.executable, "-m", "mcp_monitor", "shim", "--server-name", "demo-server",
                             "--client", "claude-desktop", "--", sys.executable, fake],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def send(msg):
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        proc.stdin.flush()

    send({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"clientInfo": {"name": "demo", "version": "1"}}})
    time.sleep(0.5)
    calls = [("echo", {"text": "hello world", "api_key": "sk-supersecretvalue12345"}),
             ("echo", {"text": "second concurrent request"}),
             ("slow", {"seconds": 1})]
    for i, (name, args) in enumerate(calls, start=1):
        send({"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}})
    for line in proc.stdout:
        msg = json.loads(line)
        if msg.get("id") in (1, 2, 3):
            result = msg["result"]["content"][0]["text"]
            print(f"request {msg['id']}: {'BLOCKED' if msg['result'].get('isError') else 'ALLOWED'} -> {result}")
        if msg.get("id") == 3:
            break
    presenter.stop()


threading.Thread(target=client, daemon=True).start()
try:
    presenter.run()
finally:
    daemon.stop()
