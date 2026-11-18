# MCP Communication Monitor

A Python app that watches the tool calls Claude makes to MCP servers and **asks you Allow or Block for each one**, in a popup that appears on top of your other windows. A blocked call is never executed, and Claude gets a clear refusal back.

Works with **Claude Desktop**, **Claude Code** and **Claude Web (claude.ai)**.

![Approval popup](docs/images/popup.png)

**Demo video:** _link added on submission_ (script in [docs/DEMO.md](docs/DEMO.md))

## How it works, in short

* An MCP tool call is a small JSON message from Claude to an "MCP server" (a helper program).
* **Claude Desktop / Claude Code**: Claude starts each local server as a child process. The installer changes the config so Claude starts a small **shim** instead. The shim starts the real server and relays messages. It **holds** every `tools/call`, asks the **daemon** (the background app) for a decision, then forwards the original message (Allow) or answers Claude itself (Block). The real server never sees a blocked call.
* **Claude Web**: Anthropic's cloud calls MCP servers, not your PC, so nothing local can see those calls. The only way in is to make the MCP address ours. A **gateway** (small web server) plus a tunnel gives claude.ai an address that asks you first and then forwards to the real server.
* If anything fails (daemon down, no answer, crash) the call is **blocked**, never silently allowed.

Full explanation with diagrams: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**. Original build plan: [plan.md](plan.md).

## What the assignment asked for, and where it is

| Requirement | Where it is met |
|---|---|
| Monitor MCP requests from Claude Desktop and Claude Web | Shim (Desktop/Code), gateway (Web) |
| Detect new MCP communication / tool calls; capture server, tool, parameters | Every `tools/call` is classified; the popup and audit log show client, server, tool, parameters; session starts are logged |
| Confirmation popup on top of other apps with relevant details | Always-on-top Tkinter popup, secrets masked, queue for many requests |
| Allow / Block | Two buttons; Esc and closing the window mean Block |
| Keep request pending; release on Allow; deny on Block | Held in the shim/gateway; Allow forwards the original message; Block returns an `isError` result |
| Multiple simultaneous requests | One popup per request, independent decisions, queue beyond 3 visible (tested with 20-24 at once) |
| Runs in background; handles termination, failures, errors | Daemon with tray icon, single instance, autostart by the shim; fail-closed; crash, timeout and cancel handling |
| Modular, documented, install/run instructions | This README, [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), docstrings, 69 tests |

## Coverage: what is intercepted, and how

| What Claude uses | Intercepted? | How |
|---|---|---|
| Local MCP server in Claude Desktop | Yes, fully (real block) | Shim wraps the server in Claude's config |
| Local MCP server in Claude Code | Yes, fully | Same shim |
| Remote MCP server you control, used from Claude Web | Yes, fully | Gateway + tunnel; add the gateway URL as the connector |
| Connector added directly in claude.ai (Figma, Gmail, Slack...) | Yes, **best effort**, in the browser only (verified with the Figma connector in Edge) | [Browser extension](extension/README.md) holds claude.ai's own "Allow once" click until you answer in our popup |
| The same connectors inside Claude Desktop / Claude Code | **No** | Run by Anthropic's cloud; nothing on the PC can see or hold the call |
| Desktop extensions (`.mcpb`) | Not tested | |

Why the split: calls from Claude Web connectors are made by Anthropic's servers, so a program on your PC can only stand in the path where the MCP address is its own (gateway), or act on the approval click that happens in your browser (extension).

## Install

Requires Python 3.10+ (developed on Windows 11 with Python 3.13).

```bash
git clone https://github.com/Ashish558/MCP-Communication-Monitor.git
cd MCP-Communication-Monitor
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[tray,gateway]"
```

`tray` adds the optional tray icon, `gateway` adds what Claude Web support needs. The core has no third-party dependencies. (`requirements.txt` in this repo is the original assignment text; dependencies are declared in `pyproject.toml`.)

## Quick try (no Claude needed)

```bash
.venv\Scripts\python scripts\popup_demo.py      # three simultaneous popups from a fake server
.venv\Scripts\python -m pytest -q               # 69 tests
```

## Use it with Claude Desktop or Claude Code

1. **Start the daemon** (the shim also starts it automatically on first use):

   ```bash
   .venv\Scripts\pythonw -m mcp_monitor daemon
   ```

2. **Route a server through the monitor.** Always preview first; `--dry-run` writes nothing:

   ```bash
   # Claude Code, project-scoped .mcp.json (only affects that project)
   .venv\Scripts\python -m mcp_monitor install --target path\to\project\.mcp.json --server myserver --dry-run
   .venv\Scripts\python -m mcp_monitor install --target path\to\project\.mcp.json --server myserver

   # Claude Desktop (edits %APPDATA%\Claude\claude_desktop_config.json, affects every Desktop chat)
   .venv\Scripts\python -m mcp_monitor install --target desktop --server filesystem
   ```

   Nothing is wrapped unless you name it (`--server NAME`, repeatable, or `--all`). Restart Claude Desktop afterwards so it reloads its servers. In Claude Code, approve the project's MCP server when it asks.

3. Use Claude normally. Each tool call opens the popup. Other commands: `list`, `doctor`, `status`, `uninstall`, `restore` (all accept `--target`).

## Use it with Claude Web (claude.ai)

```
claude.ai --HTTPS--> tunnel --> gateway (127.0.0.1:8765) --> real MCP server
                                    |
                                    +--> daemon (popup)
```

1. Start the daemon as above.
2. Start the gateway in front of a server. Give it a stdio command after `--`, or `--url` for an existing HTTP MCP endpoint:

   ```bash
   .venv\Scripts\python -m mcp_monitor gateway --name my-files --port 8765 -- npx -y @modelcontextprotocol/server-filesystem C:\some\folder
   .venv\Scripts\python -m mcp_monitor gateway --name my-api --url https://api.example.com/mcp --header "Authorization=Bearer <token>"
   ```

   It prints a connector URL ending in `/mcp/<secret-token>`.
3. Expose the port with a tunnel. Cloudflare's Quick Tunnel needs no account: `cloudflared tunnel --url http://127.0.0.1:8765` (install with `winget install Cloudflare.cloudflared`). ngrok works too.
4. In claude.ai: **Settings > Connectors > Add custom connector**, paste `https://<tunnel-host>/mcp/<token>`, choose **No sign-in**. Start a new chat, enable the connector, and ask Claude to use a tool. claude.ai shows its own permission prompt first; after you approve it, **our popup appears on your PC**.

Gateway security: secret token in the path (`--rotate-token` invalidates it), bare 404 for wrong paths, browser `Origin` check, 10 MB body limit, no access log (it would contain the token), binds to 127.0.0.1 only, daemon never exposed. Even with a leaked URL every `tools/call` still needs your click.

## Claude Web connectors you added directly (browser extension)

For connectors that were added in claude.ai itself (not through the gateway):

```bash
.venv\Scripts\python -m mcp_monitor bridge      # prints a token; keep it running
```

Then load the `extension/` folder as an unpacked extension (Chrome, Edge or Brave) and paste the token in its options. Full steps, limits and security notes: [extension/README.md](extension/README.md). In short: it works only when claude.ai shows its own approval prompt (tools set to "Always allow" are invisible to it), and it is a best-effort layer, not a hard block like the shim and gateway.

## Configuration

Optional file `%LOCALAPPDATA%\mcp-monitor\config.json`:

| Key | Default | Meaning |
|---|---|---|
| `fail_mode` | `closed` | `closed` blocks when the monitor is unavailable; `open` allows |
| `decision_timeout` | `60` | seconds before an unanswered popup auto-blocks |
| `intercept_methods` | `["tools/call"]` | add `resources/read` or `prompts/get` to cover them too |
| `max_pending` | `100` | more simultaneous requests than this are blocked at once |
| `max_visible_popups` | `3` | extra requests wait in a queue |
| `autostart_daemon` | `true` | the shim starts the daemon when needed |

Logs in the same folder: `audit.jsonl` (every request and decision), `daemon.log`, `shim.log`, `gateway.log`.

## Backups and undo

* Every config change is preceded by a backup in `backups/config/` (timestamped; the first one, `*.original.json`, is never overwritten). That folder is git-ignored because it can hold local paths.
* `python -m mcp_monitor uninstall --target <...> --all` puts the original commands back. `restore` copies the pristine backup over the config.
* **Emergency switch:** set the environment variable `MCP_MONITOR_DISABLE=1` and shims become plain pass-through.
* Editing the Desktop config affects every Desktop chat, and `~/.claude.json` every Claude Code session. For testing, prefer a project-level `.mcp.json`.

## Popup behaviour

* Always on top, taskbar flashes; one window per request, up to 3 visible, the rest queue.
* Buttons ignore clicks for about 0.6 s after the popup appears and Enter does nothing, so a stray keypress cannot approve a call. Esc or closing the window means Block.
* Parameters are shown as plain text; invisible and right-to-left control characters are escaped so a tool cannot spoof the dialog. Secrets are masked.

## Tests

`python -m pytest -q` runs 69 tests, including end-to-end tests that start the real shim and gateway against a fake MCP server (allow, block, concurrency, timeout, cancel, daemon down, server crash, large and unicode data, security checks). Manually verified: live Claude Code session, Claude Desktop, Claude Web through a Cloudflare tunnel, the Figma connector in claude.ai through the browser extension (Edge), daemon autostart, real popup timeout.

## Project layout

```
src/mcp_monitor/
  protocol.py   JSON-RPC helpers, secret masking, safe display text
  gatekeeper.py shared "ask the daemon, apply fail mode" logic
  shim.py       stdio proxy (hold / forward / deny, crash handling)
  gateway/      HTTP gateway for Claude Web (app, stdio and HTTP upstreams)
  bridge.py     local endpoint the browser extension asks for decisions
  ipc.py        proxy <-> daemon channel (127.0.0.1 + token)
  daemon/       broker (queue, timeouts, cancel), Tk popups, tray, audit log
  installer.py  backup-first config wrapping / unwrapping
  cli.py        python -m mcp_monitor <command>
extension/      claude.ai browser extension (JavaScript)
tests/          unit + end-to-end tests, fake MCP server
scripts/        popup demo, screenshot helper
docs/           ARCHITECTURE.md, DEMO.md, images
```

## Known limits

* Claude Web connectors added directly in claude.ai are covered only through the browser extension (best effort, needs claude.ai's own approval prompt). Connectors inside Claude Desktop/Code that Anthropic's cloud runs cannot be intercepted at all.
* The browser extension is a small piece of JavaScript (a browser extension cannot be written in Python); everything else is Python.
* Gateway: JSON-RPC batches and server-initiated messages are not supported (refused with a clear error, never silently allowed).
* Notifications have no reply and cannot be blocked.
* Popups open on the primary monitor. Developed and tested on Windows.
* A user with admin rights can bypass the monitor by editing the config back; `doctor` reports unmonitored servers.
* Quick Tunnel addresses change on every restart.
