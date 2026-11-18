# Configuration and reference

## Settings file

Optional: `%LOCALAPPDATA%\mcp-monitor\config.json` (all keys optional; invalid values fall back to safe defaults).

| Key | Default | Meaning |
|---|---|---|
| `fail_mode` | `"closed"` | What happens when the monitor cannot be reached. `closed` blocks; `open` allows. Anything else is treated as `closed`. |
| `decision_timeout` | `60` | Seconds before an unanswered popup is blocked automatically. |
| `intercept_methods` | `["tools/call"]` | MCP methods that need approval. Add `resources/read` and `prompts/get` to cover those too. |
| `max_pending` | `100` | More simultaneous requests than this are blocked at once. |
| `max_visible_popups` | `3` | Popups shown at the same time; the rest wait in a queue. |
| `autostart_daemon` | `true` | Lets the shim start the daemon when it is not running. |
| `preview_max_chars` | `4000` | Length limit for the parameters shown in the popup and written to the log. |
| `log_arguments` | `true` | Set `false` to keep parameters out of the audit log (decisions are still logged). |
| `button_delay_ms` | `600` | Allow and Block ignore clicks for this long after a popup appears. |
| `log_level` | `"INFO"` | Level for the component logs. |
| `disabled` | `false` | Pass-through mode (usually set with the environment variable below). |

## Environment variables

| Variable | Effect |
|---|---|
| `MCP_MONITOR_DISABLE=1` | Emergency switch: shims become plain pass-through, no popups. |
| `MCP_MONITOR_FAIL_MODE` | `closed` or `open` (overrides the file). |
| `MCP_MONITOR_TIMEOUT` | Seconds before an unanswered popup is blocked. |
| `MCP_MONITOR_HOME` | Use a different data folder (used by the tests). |

## Command line

Run as `python -m mcp_monitor <command>` (or `mcp-monitor <command>` after installation).

| Command | Purpose |
|---|---|
| `daemon [--no-tray]` | Run the background app (popups, queue, log). |
| `daemon --auto allow\|block` | Headless demo/CI mode: answer every request without a popup. Never the default. |
| `status` | Is the daemon running, and how many requests are pending. |
| `install --target T (--server NAME ... \| --all) [--dry-run]` | Route servers through the monitor. `T` is `desktop` or the path of a config file such as a project `.mcp.json`. |
| `uninstall --target T (--server NAME ... \| --all) [--dry-run]` | Put the original commands back. |
| `list --target T` | Show each server as `wrapped`, `plain` or `remote`. |
| `doctor --target T` | Check that wrapped servers are still wrapped and their Python exists. |
| `restore --target T [--from FILE]` | Copy a backup over the config (default: the pristine `*.original.json`). |
| `gateway --name N (--url URL [--header K=V] \| -- CMD...) [--port P] [--public-url U] [--rotate-token]` | HTTP gateway for Claude Web. |
| `bridge [--port P] [--rotate-token]` | Local endpoint for the browser extension (default port 8766). |
| `shim ...` | Internal: the proxy that the installer puts in front of a server. |

## Files

Everything the app writes lives in `%LOCALAPPDATA%\mcp-monitor`:

| File | Content |
|---|---|
| `audit.jsonl` | One JSON line per event: `requested`, `decided` (decision, reason, wait time), `session_start`, daemon start/stop. Rotates at 5 MB, keeps 5 files. |
| `daemon.log`, `shim.log`, `gateway.log`, `bridge.log` | Component logs (rotating). The gateway's access log is switched off so the secret URL token is not written. |
| `daemon.json` | Port and token of the running daemon (deleted on a clean exit). |
| `gateway.json`, `bridge.json` | Secret tokens. Delete a file to invalidate its token. |
| `config.json` | Your settings (optional). |

Config backups made by the installer go to `backups/config/` inside the repository when run from a checkout, otherwise to `%LOCALAPPDATA%\mcp-monitor\backups`.
