# Testing

## Run the tests

```bash
.venv\Scripts\python -m pytest -q          # 97 tests, about 40 seconds
.venv\Scripts\python -m pytest -k block -v # only the tests about blocking, listed by name
```

The tests use a temporary data folder and a rule-driven stand-in for the popup, so they never touch your real logs, config or Claude apps. A passing "block" test means the request really was blocked.

Captured results of the latest run: [test-results.md](test-results.md).

## What the automated tests cover

| File | Tests | What it checks |
|---|---|---|
| `tests/test_protocol.py` | 12 | Message classification, denial replies, id handling, secret masking, text sanitising, previews. |
| `tests/test_integration.py` | 17 | The real shim process against a fake MCP server and an in-process daemon: allow, block, concurrency, timeout, cancel, daemon down and stopped, server crash, batches, large and unicode data, garbage input, the disable switch. |
| `tests/test_gateway.py` | 20 | The real HTTP gateway: sessions, tokens, origins, concurrency, hang-ups, cancel, crash, daemon down, HTTP upstream including streamed replies. |
| `tests/test_bridge.py` | 9 | The browser bridge: token, origins (Chrome and Edge), size limits, masking, fail closed. |
| `tests/test_installer.py` | 12 | Config wrapping and unwrapping, backups, dry run, undo, corrupt files, Store-install path. |
| `tests/test_hook.py` | 24 | The Claude Code hook: allow and block answers, popup details, every failure path blocks, the real hook process, and the settings-file installer. |
| `tests/test_audit.py` | 2 | What is written to the audit log, and the privacy switch. |
| `tests/test_broker.py` | 1 | The cap on simultaneous requests. |

`tests/fake_mcp_server.py` is a small stdio MCP server with tools `echo`, `slow`, `crash`, `count` and `big`. It is also used for the manual demos.

## What was verified by hand on real software

These cannot run in an automated test because they need the real Claude apps and a person at the screen.

| Check | Result |
|---|---|
| Claude Code (terminal and project `.mcp.json`): allow and block | Works |
| Claude Desktop, agent-mode session: allow, block, timeout | Works. The chat tab starts the same shim; a tool call from the chat tab itself was not observed. |
| Claude Web through the gateway and a Cloudflare tunnel: allow and block | Works |
| Figma connector in claude.ai through the browser extension (Edge): allow and block | Works |
| Figma connector in a terminal Claude Code session **without** the hook | Works; the monitor sees nothing (this is why the hook was added) |
| Same call **with the hook**, daemon set to block everything | Call stopped; Claude was told "Blocked by MCP Monitor" |
| With the hook, daemon set to allow everything | Call ran and returned data |
| With the hook, no daemon and autostart off | Call stopped (fails closed) |
| With the hook, real popup: Allow, then Block | Allow ran the call; Block stopped it ("Blocked by the user") |
| Same hook in **Claude Desktop's Code tab** (new session on the project), Figma connector: Allow, then Block | Popup appeared both times; Allow ran, Block stopped it (audit log: `claude-code (hook)`). The connector is named by an id there, shown as `claude.ai connector (xxxxxxxx)` |
| A server covered by both the shim and the hook, in a Code session | Two popups per call, hook first then shim (use `hook_skip_servers` to avoid it) |
| Hook in IDE extensions | Not verified |
| Shim starts the daemon by itself, including after Claude Desktop restarted | Works |
| A real popup left unanswered | Blocked after the timeout |
| Popup on high-DPI screen (buttons not clipped) | Fixed and checked |
| Extension logic against a mock of claude.ai's prompt (`extension/test/mock-claude.html`) | Allow, Block, bridge error, "Always allow", Ctrl+Enter, Decline |

To try the popup without any Claude app: `python scripts/popup_demo.py` (three simultaneous popups) and `python scripts/capture_screenshot.py` (renders the screenshot used in the README).

## Not tested

Other operating systems, fullscreen applications, multiple monitors, locked or sleeping PCs, and the Tkinter UI code and tray icon under automation. See [edge cases](edge-cases.md).
