# Setup

Set up only the parts for the kinds of tools you use.

| Step | What you set up | Why it is needed |
|---|---|---|
| 1 | **Install** the app and start the **daemon** | The daemon is the background app that shows the popup and keeps the log. Every other part asks it for a decision. |
| 2 | **Local tools** in Claude Desktop / Claude Code: wrap them with the installer | Claude starts these tools itself, so a small program (the shim) can sit in the middle and hold each request. |
| 3 | **Your own tools for Claude Web**: run the gateway and a tunnel, add the tunnel address as a connector | Claude Web calls tools from Anthropic's cloud, so the tool's address has to be ours for us to see the request. |
| 4 | **Tools added in the Claude website** (Figma, Gmail, Slack...): run the bridge and load the browser extension | Anthropic's servers call these tools directly, so neither the shim nor the gateway can see them. The only thing that happens on your PC is the website's own "Allow once" prompt. The extension holds that click and asks the daemon first. **Without step 4, these tools run with no popup.** |
| 5 | **claude.ai connectors used inside Claude Code**: register the Claude Code hook | Claude Code runs a hook before every tool call, including these connectors. Our hook asks the daemon first, so a block really stops the call. **Without step 5, these connectors run with no popup in Claude Code.** |

## Prerequisites

* Python 3.10 or newer (developed on Windows 11 with Python 3.13).
* For step 3: a tunnel program. Cloudflare's Quick Tunnel needs no account (`winget install Cloudflare.cloudflared`). ngrok also works.
* For step 4: Chrome, Edge or Brave.

## Step 1: Install and start the daemon

```bash
git clone https://github.com/Ashish558/MCP-Communication-Monitor.git
cd MCP-Communication-Monitor
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[tray,gateway]"
```

The `tray` extra adds an optional tray icon. The `gateway` extra adds `aiohttp`, needed for steps 3 and 4. The core (shim, daemon, installer) has no third-party dependencies.

Start the daemon. It runs in the background; the shim also starts it automatically the first time it is needed.

```bash
.venv\Scripts\pythonw -m mcp_monitor daemon
.venv\Scripts\python -m mcp_monitor status        # should say "running"
```

Try it without any Claude app: `.venv\Scripts\python scripts\popup_demo.py` opens three simultaneous popups from a fake MCP server.

## Step 2: Local tools in Claude Desktop or Claude Code

Route a server through the monitor. Always preview first; `--dry-run` writes nothing.

```bash
# Claude Code: a project-scoped .mcp.json only affects that project
.venv\Scripts\python -m mcp_monitor install --target path\to\project\.mcp.json --server myserver --dry-run
.venv\Scripts\python -m mcp_monitor install --target path\to\project\.mcp.json --server myserver

# Claude Desktop: affects every Desktop chat
.venv\Scripts\python -m mcp_monitor install --target desktop --server filesystem
```

* Nothing is wrapped unless you name it (`--server NAME`, repeatable, or `--all`).
* For `--target desktop` the installer finds Claude Desktop's config file itself, including the Microsoft Store build's location (`%LOCALAPPDATA%\Claude-Data\claude_<id>\claude_desktop_config.json`). The server must already be listed under `mcpServers` in that file.
* Restart Claude Desktop afterwards so it reloads its servers. In Claude Code, approve the project's MCP server when it asks.
* Check the result with `list --target ...` (wrapped or plain) and `doctor --target ...`.

Use Claude normally. Each tool call opens the popup. The "From" line shows the client, for example `claude-desktop (...)` or `claude-code (...)`.

## Step 3: Your own tools for Claude Web

```
claude.ai --HTTPS--> tunnel --> gateway (127.0.0.1:8765) --> real MCP server
                                    |
                                    +--> daemon (popup)
```

1. Start the gateway in front of a server. Give it a stdio command after `--`, or `--url` for an existing HTTP MCP endpoint:

   ```bash
   .venv\Scripts\python -m mcp_monitor gateway --name my-files --port 8765 -- npx -y @modelcontextprotocol/server-filesystem C:\some\folder
   .venv\Scripts\python -m mcp_monitor gateway --name my-api --url https://api.example.com/mcp --header "Authorization=Bearer <token>"
   ```

   It prints a connector URL ending in `/mcp/<secret-token>`.
2. Expose the port with a tunnel: `cloudflared tunnel --url http://127.0.0.1:8765`. It prints a public `https://....trycloudflare.com` address.
3. In claude.ai: **Settings > Connectors > Add custom connector**. Paste `https://<tunnel-host>/mcp/<token>` and choose **No sign-in**.
4. Start a new chat, switch the connector on, and ask Claude to use a tool. claude.ai shows its own permission prompt first; after you approve it, **our popup appears on your PC**.

Quick Tunnel addresses change every time the tunnel restarts. If the connector stops working, paste the new address in the connector settings and start a new chat.

## Step 4: Tools added directly in the Claude website

**Why this step exists:** a connector added in claude.ai (Figma, Gmail, Slack...) is called by Anthropic's servers, not through the gateway, so steps 2 and 3 cannot see it. The extension is the only part that covers these tools. It is not Figma-specific: it works for any tool added in the website.

The extension code is in the [`extension/`](../extension/) folder of this repo.

1. Start the bridge. It prints a token:

   ```bash
   .venv\Scripts\python -m mcp_monitor bridge
   ```
2. Open `chrome://extensions` (or `edge://extensions`, `brave://extensions`), switch on **Developer mode**, click **Load unpacked**, and choose the `extension` folder.
3. Open the extension's **Options**, paste the token, and click **Save and test**. It should say "Connected".
4. In claude.ai, make sure the connector's tools are set to **Needs approval** (Settings > Connectors > the connector > tool permissions). A tool set to "Always allow" runs without a prompt, so the extension has nothing to hold.
5. Use the connector. After you click **Allow once** on claude.ai's prompt, our popup appears; **Allow** lets the call run, and **Block** makes the extension click "Decline".

This step is best effort: it works only when claude.ai shows its own approval prompt. More detail and limits: [extension/README.md](../extension/README.md).

## Step 5: claude.ai connectors used inside Claude Code

**Why this step exists:** connectors such as Figma, Gmail or Slack are called through Anthropic's servers, so the shim and gateway cannot see them. Claude Code, however, runs a **`PreToolUse` hook before every tool call**, including these connectors (their tool names look like `mcp__claude_ai_Figma__whoami`). MCP Monitor registers itself as that hook, so the same popup appears and a block really stops the call. It is not Figma-specific: it gates every MCP tool that matches.

1. Start the daemon (step 1).
2. Register the hook in a Claude Code settings file. Preview first. **Project** scope affects only that project; **user** scope affects every Claude Code session:

   ```bash
   cd path\to\your\project
   .venv\Scripts\python -m mcp_monitor hook-install --target project --dry-run
   .venv\Scripts\python -m mcp_monitor hook-install --target project
   .venv\Scripts\python -m mcp_monitor hook-install --target user      # every Claude Code session
   ```

   Other targets: `local` (`.claude/settings.local.json`) or a file path.
3. Use Claude Code normally. Each MCP tool call opens the popup with `claude-code (hook)` in the "From" line. **Allow** lets it run, and Claude Code does not ask a second time. **Block** stops it and Claude is told "Blocked by MCP Monitor".

Things to know:

* Only MCP tools are gated; Bash, Edit and other built-in tools are left alone. Narrow it with `--matcher` (a regex on the tool name), for example `mcp__claude_ai_.*` for claude.ai connectors only.
* **Avoid double popups.** A server already wrapped by the shim (step 2) is also an MCP tool. Skip it with a matcher such as `mcp__(?!myserver__).*`, or list it under `hook_skip_servers` in the [configuration](configuration.md).
* **Fails closed.** The hook exits with code 2 (Claude Code's "block") on any problem. The installer also sets `"onFailure": "block"` (Claude Code 2.1.295 or newer), so even a hook that cannot start blocks the call.
* Claude Code's own deny and ask rules still apply on top of the hook.
* Check with `hook-status --target ...`; undo with `hook-uninstall --target ...` (a backup is made first). Claude Code picks up settings-file changes by itself; restart a running session if it does not.

## Undo and recovery

* Every config change is preceded by a backup in `backups/config/` (timestamped; the first one, `*.original.json`, is never overwritten). That folder is git-ignored because it can hold local paths.
* `python -m mcp_monitor uninstall --target <...> --all` puts the original commands back. `restore --target <...>` copies the pristine backup over the config.
* The Claude Code hook is removed with `python -m mcp_monitor hook-uninstall --target <...>`.
* **Emergency switch:** set the environment variable `MCP_MONITOR_DISABLE=1` and shims become plain pass-through (no popups).
* Editing the Desktop config affects every Desktop chat, and `~/.claude.json` every Claude Code session. For testing, prefer a project-level `.mcp.json`.
* To stop everything: quit the daemon from its tray icon (or end its process), stop the gateway, bridge and tunnel, and remove the extension from the browser.

## Troubleshooting

| Problem | Likely cause and fix |
|---|---|
| No popup in Claude Desktop | Run `status` (is the daemon running?) and `doctor --target desktop` (is the server still wrapped?). Restart Claude Desktop. |
| The popup says `claude-code` when you expected Desktop | The request came from a Claude Code session using a project `.mcp.json`, not from Claude Desktop. |
| Claude says the monitor is not running | This is the safe default (fail closed). Start the daemon and ask again. |
| Web connector shows a 502 or "server is down" | The gateway or tunnel is not running, or the tunnel address changed. Restart them, update the connector address, and use a new chat (an old chat remembers the failure). |
| Figma (or another website tool) runs with no prompt | A tool is on "Always allow". Set it to "Needs approval" and refresh the claude.ai tab. |
| Extension options say HTTP 401 or "no token" | Paste the token printed by `bridge`; run `bridge --rotate-token` for a new one. |
| Extension options say HTTP 403 | Update to the latest extension and bridge (Edge uses an `extension://` origin, which the bridge accepts). |
| A request is blocked with "No answer within 60 seconds" | Click within the timeout, or raise `decision_timeout` ([configuration](configuration.md)). |
| The hook shows no popup in Claude Code | Check `hook-status`, that the matcher covers the tool, and `%LOCALAPPDATA%\mcp-monitor\hook.log`. Hooks are read from settings files, so run it in the project where it was installed (or use `--target user`). |
| Two popups for one tool call in Claude Code | The server is covered by both the shim and the hook. Add it to `hook_skip_servers` or change the matcher. |
