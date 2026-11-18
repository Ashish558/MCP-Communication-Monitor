# Plan B — Fallback Plan (use if the main plan in ../plan.md hits a blocker)

Same goal: hold every MCP call, ask the user Allow/Block, forward or deny.
Fail-closed stays the default.

## When to switch to Plan B
| Trigger | Switch to |
|---|---|
| Editing the real Claude config disrupts other sessions or breaks servers | B1 — isolated test setup |
| Claude Desktop will not use the wrapped config / extensions cannot be wrapped | B2 — launcher-based wrapping |
| Claude Web tunnel/gateway unreliable or not finished by Fri morning | B3 — ship Desktop/Code only, document Web |
| Popup cannot stay on top reliably with Tkinter | B4 — alternate UI |
| Time runs out | B5 — minimum submission |

## B1 — Isolated setup (zero interference with other sessions)
- Never edit `~/.claude.json` or the global Desktop config.
- For Claude Code: use a **project-scoped `.mcp.json`** in a dedicated test folder, or start Claude Code with `--mcp-config <our-file>` (optionally `--strict-mcp-config`) so only the wrapped servers load for that one session.
- For Desktop: test on a copy first; only wrap one throwaway server; keep the backup in `backups/config/`.
- Emergency switch: `MCP_MONITOR_DISABLE=1` makes shims pass-through.

## B2 — Launcher-based wrapping (if the config rewrite approach fails)
- Instead of editing each entry, provide a small `mcp-monitor-run <original command...>` launcher and document how to paste it into the config by hand.
- Same shim code underneath; only the installation step changes (manual, with copy-paste examples in the README).

## B3 — Claude Web fallback
- Ship a working gateway exercised with a **local MCP client** (or MCP Inspector) over HTTP, showing the same Allow/Block behaviour.
- Document exact steps to attach it to claude.ai via a tunnel, plus known limits.
- Be explicit in the README and video about what was verified against real claude.ai and what was not.

## B4 — Alternate popup
- Order of fallbacks: Tkinter topmost → Windows native `MessageBox` with `MB_TOPMOST | MB_SETFOREGROUND` via `ctypes` (very reliable on top, simple Allow/Block mapped to Yes/No) → toast notification with action buttons.
- Keep the UI behind an interface so swapping is cheap.

## B5 — Minimum acceptable submission (if time is very short)
1. Stdio shim + daemon + popup working with Claude Code and/or Claude Desktop (allow + block + concurrent).
2. README with setup, architecture, and honest limits.
3. Demo video: one approved call, one blocked call.
4. Claude Web described as a documented gateway design with whatever part is working.

## Recovery checklist (if something breaks)
1. Close Claude Desktop / Claude Code.
2. Copy the newest file in `backups/config/` back over the live config (or run `installer restore`).
3. Set `MCP_MONITOR_DISABLE=1` if the shim must stay installed.
4. Restart Claude and confirm the servers list loads.
