# Demo video: script and checklist

Goal: show **a request being approved** and **a request being blocked**, in Claude Desktop and Claude Web. Aim for 4 to 5 minutes. Record with Xbox Game Bar (`Win+G`), OBS, or any screen recorder.

## Before recording

1. Start from a clean state: close extra windows, hide anything private (email, chats).
2. Start the daemon: `.venv\Scripts\pythonw -m mcp_monitor daemon` (a small orange tray icon appears).
3. **Claude Desktop**: wrap one harmless server (the fake server or a filesystem server pointing at an empty demo folder):
   `.venv\Scripts\python -m mcp_monitor install --target desktop --server <name>` then restart Claude Desktop.
4. **Claude Web**: start the gateway and tunnel, add the connector once (see README, "Claude Web"). Use the fake server, not real data.
5. **Claude Web, connectors added directly in claude.ai (Figma):**
   * Start the bridge: `.venv\Scripts\python -m mcp_monitor bridge` (it prints a token).
   * In Edge/Chrome/Brave: `edge://extensions` (or `chrome://extensions`) > Developer mode > **Load unpacked** > the `extension` folder. Open its **Options**, paste the token, **Save and test** (should say "Connected").
   * In claude.ai: Settings > Connectors > Figma > tool permissions: set the tools to **Needs approval** (a tool set to "Always allow" runs without a prompt, so the extension cannot see it). Refresh the claude.ai tab.
6. Keep the terminal font large enough to read.
7. Hide or blur the connector URL and both tokens (gateway URL, bridge token) on screen. After recording, rotate them (see "After recording").

## Script

| # | Time | What you do | What to say |
|---|---|---|---|
| 1 | 0:00 | Show the GitHub repo and the README diagram. | "This app sits between Claude and its MCP servers and asks me to allow or block every tool call." |
| 2 | 0:30 | Terminal: `python -m mcp_monitor list --target desktop`, then `doctor`. Show the server is "wrapped / monitored". | "The installer routes a server through a small proxy. It makes a backup first, and can undo it." |
| 3 | 1:00 | **Claude Desktop, approve.** Ask: *Use the `<tool>` tool to ...* The popup appears on top of other windows. Point at client, server, tool, parameters. Click **Allow**. | "The call is held. I can see who is asking, which server, which tool and the parameters, with secrets masked. I click Allow and Claude gets the result." |
| 4 | 1:45 | **Claude Desktop, block.** Ask again, click **Block**. | "Blocked. The server never ran it, and Claude gets a clear refusal it can explain." |
| 5 | 2:15 | **Concurrent requests.** Ask Claude to call the tool several times in one message (or ask for two tools at once). Show the stack of popups, answer one Allow and one Block. | "Several requests at once each get their own popup. They are decided independently." |
| 6 | 2:45 | **Timeout (optional).** Leave a popup alone and show the countdown reaching zero. | "No answer means blocked. The monitor fails closed." |
| 7 | 3:00 | **Claude Web.** In claude.ai, use the gateway connector. Show claude.ai's own prompt, then our popup. **Allow** once, **Block** once. | "Claude Web calls the server from Anthropic's cloud, so I route the connector through my gateway. Same popup, same control." |
| 7b | 3:45 | **Claude Web, Figma (browser extension).** Set the Figma tools to "Needs approval" first. Ask Claude about a Figma file, click **Allow once** in claude.ai, then show our popup. Click **Allow** once and **Block** once. | "Connectors added directly in claude.ai run in Anthropic cloud, so I hold the approval click in the browser with a small extension and ask through the same popup." |
| 8 | 4:00 | Show `audit.jsonl` (open the log folder from the tray icon) with the decisions. | "Every request and decision is logged." |
| 9 | 4:20 | Run `python -m pytest -q` (69 tests pass). | "The core logic is covered by automated tests." |
| 10 | 4:40 | Close: mention the limits and the docs (README coverage table, docs/ARCHITECTURE.md). | "Local servers are fully blocked. Claude Web is covered through the gateway, and through a best-effort browser extension for connectors added in claude.ai. The same connectors inside Claude Desktop or Claude Code run in Anthropic's cloud and cannot be intercepted." |

Tip: in claude.ai choose **Allow once** each time (not "Always allow") so both prompts are visible.

## After recording

* Upload the video (unlisted YouTube, Google Drive or Loom) and paste the link into the README under "Demo video".
* Stop the gateway, the tunnel (Cloudflare Quick Tunnel addresses die with the process) and the bridge.
* Rotate the secrets you showed: `gateway ... --rotate-token` and `bridge --rotate-token` (then re-paste the new bridge token in the extension options if you keep using it).
* Optionally restore the Desktop config: `python -m mcp_monitor uninstall --target desktop --all`.
* Optionally remove the extension from the browser's extensions page.
