# MCP Communication Monitor

Python app that sits between Claude and its MCP servers and asks you **Allow** or **Block** for every tool call, in a popup that appears on top of your other windows. A blocked call is never executed; Claude receives a clear denial instead.

> **Status:** local stdio interception (Claude Code app + Claude Desktop local servers) is implemented and verified in a live Claude Code session. The Claude Web gateway is implemented and covered by automated tests; testing it against real claude.ai through a tunnel is the next step. See [plan.md](plan.md).

## How it works (short)

```
Claude Desktop / Claude Code --stdio--> [shim] --stdio--> real MCP server
                                           |
                                           +--local socket--> [daemon: queue + popup + audit log]
```

* MCP messages are JSON-RPC lines. Claude starts each local MCP server as a child process and talks to it through stdin/stdout.
* The installer rewrites a server's entry in Claude's config so Claude starts the **shim** instead. The shim starts the real server itself and relays every message.
* A `tools/call` request is **held**. The shim asks the **daemon**, which shows the popup. *Allow* forwards the original message untouched; *Block* answers Claude directly with an `isError` tool result ("Blocked by the user...") and the real server never sees the call.
* Everything else (`initialize`, `tools/list`, notifications) passes through unchanged, so it works across MCP protocol versions.
* Failure default is **fail closed**: if the daemon is unreachable, crashes, or nobody answers within the timeout, the call is blocked.

Full design, edge cases and security notes: [plan.md](plan.md). Fallback plan: [backups/plan-b-fallback.md](backups/plan-b-fallback.md).

## Install

Requires Python 3.10+ (tested on 3.13, Windows 11).

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[tray]"      # tray icon is optional; drop [tray] to skip it
```

## Run

1. **Start the daemon** (background app; shims also start it automatically on first use):

   ```bash
   .venv\Scripts\pythonw -m mcp_monitor daemon
   ```

2. **Route an MCP server through the monitor.** Preview first, nothing is written with `--dry-run`:

   ```bash
   # Claude Code: a project-scoped .mcp.json only affects that project
   .venv\Scripts\python -m mcp_monitor install --target path\to\project\.mcp.json --server myserver --dry-run
   .venv\Scripts\python -m mcp_monitor install --target path\to\project\.mcp.json --server myserver

   # Claude Desktop (%APPDATA%\Claude\claude_desktop_config.json) - affects every Desktop chat
   .venv\Scripts\python -m mcp_monitor install --target desktop --server filesystem
   ```

   Restart the Claude app so it reloads its servers. Nothing is wrapped unless you name it (`--server NAME`, repeatable, or `--all`).

3. Use Claude as normal. Each tool call shows a popup with the client, MCP server, tool name and parameters (secrets masked).

Other commands: `list`, `doctor`, `status`, `uninstall`, `restore` (all take `--target`).

## Claude Web (claude.ai) through the gateway

Claude Web calls MCP servers from Anthropic's cloud, not from your PC, so nothing on your machine can see those calls. The only place to intercept is the MCP endpoint itself: the **gateway** is that endpoint. It speaks MCP "Streamable HTTP" to Claude, asks you in the same popup, and forwards approved calls to the real server.

```
claude.ai --HTTPS--> tunnel --> [gateway 127.0.0.1:8765] --> real MCP server (stdio child or upstream URL)
                                      |
                                      +--> [daemon: popup, queue, audit log]
```

1. `pip install -e ".[gateway]"`, and start the daemon as above.
2. Start the gateway in front of a server (stdio command after `--`, or `--url` for an existing HTTP MCP endpoint):

   ```bash
   .venv\Scripts\python -m mcp_monitor gateway --name my-files --port 8765 -- npx -y @modelcontextprotocol/server-filesystem C:\some\folder
   .venv\Scripts\python -m mcp_monitor gateway --name my-api --url https://api.example.com/mcp --header "Authorization=Bearer <token>"
   ```
3. Expose port 8765 with a tunnel, for example Cloudflare's quick tunnel (no account needed): `cloudflared tunnel --url http://127.0.0.1:8765`
4. In claude.ai: Settings > Connectors > Add custom connector, URL = `https://<tunnel-host>/mcp/<token>` (the gateway prints the token path; `--public-url https://<tunnel-host>` makes it print the full URL).

Gateway security: the URL contains a secret token (`--rotate-token` invalidates it); wrong paths return a bare 404; requests carrying a browser `Origin` other than claude.ai/claude.com are rejected; bodies over 10 MB are rejected; the access log is disabled because it would contain the token; the gateway binds to 127.0.0.1 only and the daemon port is never exposed. Even with a leaked URL, every `tools/call` still needs your click. Not supported (refused with a clear error, never silently allowed): JSON-RPC batches and server-initiated messages (GET streams). Connectors added directly in claude.ai, without this gateway, are not covered.

## Undo / safety net

* Every config change is preceded by a backup in `backups/config/` (timestamped; the first backup, `*.original.json`, is never overwritten).
* `python -m mcp_monitor uninstall --target <...> --all` puts the original commands back; `restore` copies the pristine backup over the config.
* Emergency off switch: set the environment variable `MCP_MONITOR_DISABLE=1` and shims become pure pass-through.
* Editing the global Claude Desktop config affects every chat, and `~/.claude.json` every Claude Code session; for testing prefer a project-level `.mcp.json`.

## Configuration

`%LOCALAPPDATA%\mcp-monitor\config.json` (all optional):

| Key | Default | Meaning |
|---|---|---|
| `fail_mode` | `closed` | `closed` blocks when the monitor is unavailable; `open` allows |
| `decision_timeout` | `60` | seconds before an unanswered popup auto-blocks |
| `intercept_methods` | `["tools/call"]` | add `resources/read`, `prompts/get` to cover them too |
| `max_pending` | `100` | more simultaneous requests than this are auto-blocked |
| `max_visible_popups` | `3` | extra requests wait in a queue |
| `autostart_daemon` | `true` | shim starts the daemon when needed |

Logs in the same folder: `audit.jsonl` (every request and decision), `daemon.log`, `shim.log`.

## Popup behaviour

* Always on top, taskbar flashes; one window per request, up to 3 visible, the rest queue.
* Buttons are inert for ~0.6 s after appearing and Enter does nothing, so a stray keypress cannot approve a call. Esc or closing the window = Block.
* Parameters are shown as plain text with invisible/bidi control characters escaped, so a tool cannot spoof the dialog.

## Tests and demo

```bash
.venv\Scripts\python -m pytest -q                    # 58 tests: protocol, installer, shim and gateway end-to-end with a fake MCP server
.venv\Scripts\python scripts\popup_demo.py           # shows 3 real concurrent popups
```

## Layout

```
src/mcp_monitor/
  protocol.py   JSON-RPC helpers, redaction, display sanitising
  shim.py       stdio proxy (hold / forward / deny, crash handling)
  gatekeeper.py shared "ask the daemon, apply fail mode" logic
  gateway/      HTTP gateway for Claude Web (app, stdio/HTTP upstreams)
  ipc.py        shim <-> daemon channel (127.0.0.1 + token)
  daemon/       broker (queue, timeouts, cancel), Tk popups, tray, audit log
  installer.py  backup-first config wrapping / unwrapping
  cli.py        python -m mcp_monitor <command>
tests/          unit + end-to-end tests, fake MCP server
```

## Known limits

* Claude Web calls MCP servers from Anthropic's cloud, so it is only covered for connectors routed through the gateway; connectors added directly in claude.ai bypass the monitor.
* Notifications have no reply and cannot be blocked; JSON-RPC batches are decided per item.
* Popups open on the primary monitor.
* A user with admin rights can bypass the monitor by editing the config back; `doctor` reports unmonitored servers.

> `requirements.txt` in this repo is the original assignment text, not a pip requirements file. Dependencies are declared in `pyproject.toml`.
