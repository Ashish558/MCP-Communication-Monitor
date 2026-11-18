# MCP Communication Monitor — Implementation Plan

Status: PLANNING ONLY. No code is written until the owner says so.
Deadline: Friday, 20 November 2026, EOD IST (submit CV + GitHub repo link + demo video link).

---

## 1. Objective (from requirements.txt)

A Python application that watches the MCP (Model Context Protocol) calls made by Claude Desktop and Claude Web, holds each call until the user clicks **Allow** or **Block**, and then either lets the call through or returns a denial to Claude.

### Decisions already made with the owner
| Decision | Choice |
|---|---|
| Claude Web support | **Required** (via gateway), built **after** the local app is tested |
| Failure default | **Fail-closed**: if anything goes wrong, the request is blocked |
| Build order | Local stdio first (Claude Code app as test bed, then Claude Desktop), then Claude Web gateway |
| UI toolkit | Tkinter (no extra dependencies), unless a blocker appears |

---

## 2. Plain-language concepts used in this plan

- **MCP server**: a helper program Claude asks to do things (read files, search, etc.).
- **JSON-RPC**: the text format of each request ("note") Claude sends to an MCP server. Contains method (e.g. `tools/call`), tool name, and arguments.
- **stdin/stdout**: two pipes a program uses to receive and send text. Claude Desktop and Claude Code talk to local MCP servers through these pipes.
- **Shim / proxy**: our middleman program placed between Claude and the real MCP server.
- **Daemon**: our background program that owns the popup and the decisions.
- **Gateway**: the web-facing version of the middleman, needed for Claude Web.
- **Tunnel**: a tool (cloudflared/ngrok) that gives the PC a public address so Anthropic's servers can reach our gateway.

---

## 3. Interception strategy

### 3.1 Why a proxy
MCP traffic is plain JSON-RPC text between two programs. The only reliable way to *pause and block* a call is to be the program in the middle. Network sniffing (encrypted), process hooking or injecting into the Claude app (fragile, may break terms), and browser extensions (observe only, cannot block server-side calls) are rejected.

### 3.2 Entry point A — Stdio shim (Claude Code app and Claude Desktop, local servers)
1. The client's config lists each MCP server as `command` + `args`.
2. Our installer rewrites a chosen server entry so the command is **our shim**, and stores the original command as arguments to the shim.
3. When the client starts the "server", it starts our shim. The shim starts the real server as a child process.
4. The shim relays messages both ways. For each message coming **from Claude**:
   - `tools/call` (and optionally `resources/read`, `prompts/get`) → ask the daemon for a decision and hold the message.
   - Everything else (`initialize`, `tools/list`, pings, notifications) → pass through untouched.
5. **Allow** → forward the original message to the real server, relay its reply.
6. **Block** → do NOT forward. Reply to Claude ourselves: for `tools/call` a normal result with `isError: true` and text "Blocked by user via MCP Monitor"; for other methods a JSON-RPC error. Using `isError` lets the model read the denial and explain it to the user.

Config files involved:
- Claude Desktop: `%APPDATA%\Claude\claude_desktop_config.json` (key `mcpServers`). Desktop extensions (`.mcpb`/`.dxt`) are launched differently and must be investigated (see Risks).
- Claude Code: project `.mcp.json` (project scope) or `~/.claude.json` (user/local scope). **Project scope is the safe test bed** (see §9).

### 3.3 Entry point B — HTTP gateway (Claude Web, and Desktop remote connectors)
Claude Web calls MCP servers **from Anthropic's cloud**, so nothing local can see those calls. The only controllable point is the MCP endpoint itself.
1. Our gateway exposes an MCP endpoint (Streamable HTTP, with SSE fallback if needed) and forwards to the real upstream MCP server (HTTP URL, or a local stdio server that the gateway launches).
2. A tunnel exposes the gateway at a public HTTPS address.
3. The user adds that address as a custom connector in claude.ai.
4. Same interception logic: hold `tools/call`, ask the daemon, forward or deny.
5. The popup still appears on the user's PC because the gateway runs on the user's PC and talks to the same daemon.

Honest limitation (to be stated in README): Claude Web is covered only for connectors routed through our gateway. Connectors the user adds directly to claude.ai bypass us.

### 3.4 Shared core
Both entry points use the same **protocol layer** (parse/frame JSON-RPC, track request IDs) and the same **decision client** (talk to the daemon), so behaviour is identical.

---

## 4. Architecture

```
Claude Code / Desktop --stdio--> [Shim] --stdio--> Real MCP server
Claude Web --HTTPS--> Tunnel --> [Gateway] --> Real MCP server
            Shim / Gateway --IPC "allow?"--> [Daemon: queue + popup + log]
```

### 4.1 Components
| Component | Responsibility |
|---|---|
| `shim` | Stdio proxy per MCP server. Parses messages, asks the daemon, forwards or denies. |
| `gateway` | HTTP MCP proxy for Claude Web. Same logic as the shim over HTTP. |
| `daemon` | Background tray app. Receives requests, keeps the pending queue, shows popups, returns decisions, writes the audit log. |
| `ui` | Popup window(s): always on top, shows server, tool, parameters, source client. |
| `ipc` | Local channel between shim/gateway and daemon: `127.0.0.1` socket, per-install random auth token stored in a user-only file. |
| `installer` | Backs up configs, wraps/unwraps chosen servers, restores originals, `--dry-run`. |
| `config` | Settings: fail mode, timeout, which methods to intercept, redaction patterns, log path. |
| `log` | Audit log (JSON lines) of every request and decision. Rotating. |

### 4.2 Proposed layout
```
mcp communication monitor/
  README.md
  plan.md
  requirements.txt            (existing assignment text; Python deps go in pyproject.toml / requirements-dev)
  pyproject.toml
  backups/                    (plan backups + config backups, see §9)
  src/mcp_monitor/
    protocol/    (framing, jsonrpc models, method classification)
    shim/        (stdio proxy)
    gateway/     (HTTP proxy)
    daemon/      (queue, decision engine, tray)
    ui/          (popup)
    ipc/         (client + server, auth)
    installer/   (config discovery, backup, wrap/unwrap)
    config.py  logging_setup.py
  tests/
    fake_mcp_server/  (test server with echo/slow/crash tools)
    unit/ integration/
  docs/  (architecture, screenshots, demo link)
```
Note: the assignment file is named `requirements.txt`; dependency listing will use `pyproject.toml` to avoid clashing with it, unless the owner prefers renaming.

### 4.3 Request lifecycle
1. Claude sends `tools/call` with id `N`.
2. Shim creates a pending record `{request_id (unique across all shims), client, server_name, tool, args, time}`.
3. Shim sends it to the daemon and **holds** the message; other traffic keeps flowing.
4. Daemon queues it and shows a popup.
5. User clicks Allow/Block (or timeout / cancel / disconnect resolves it).
6. Daemon replies; shim forwards or denies; log written.
7. Pending record removed.

---

## 5. Requirements traceability

| # | Requirement | How it is met |
|---|---|---|
| 1.1 | Monitor outgoing MCP requests from Claude Desktop and Web | Shim (Desktop/Code), gateway (Web) |
| 1.2 | Detect new MCP communication/tool invocation | Classify each message by `method`; `tools/call` = invocation. Also log server start (`initialize`) as "new MCP communication" |
| 1.3 | Capture server, tool, parameters | Server from config key/connector name; tool and arguments from the request params |
| 2.1 | Popup on top of other apps | Tkinter topmost window, raised and focused, taskbar flash fallback |
| 2.2 | Popup shows relevant info | Source client, server, tool, formatted params (truncated, secrets masked, "show full" toggle), time, queue position |
| 2.3 | Allow / Block | Two buttons; Block is the default/safe action |
| 3.1 | Keep request pending until decision | Held in shim/gateway; nothing sent upstream |
| 3.2 | Allow → release | Forward original message |
| 3.3 | Block → error/denial to caller | `isError` tool result or JSON-RPC error |
| 3.4 | Multiple simultaneous requests | Per-request IDs, async handling, queue UI, independent decisions |
| 4.1 | Run in background | Daemon with tray icon; optional start-at-login |
| 4.2 | Popup above other windows | See 2.1 |
| 4.3 | Handle termination, comms failures, errors | See §6 and §7 (fail-closed everywhere) |
| T | Modular, documented, instructions | Layout above, docstrings, README |
| D | README, architecture explanation, demo video | §10, §11 |

---

## 6. Edge cases and handling

### 6.1 Concurrency
| Case | Handling |
|---|---|
| Many requests at once (same or different servers) | Each gets a unique ID and its own card; decisions are independent; queue shown with count |
| Request flood (e.g. 50 calls in a second) | Cap on visible cards; "Block all / Allow all from this server" bulk actions, explicit confirmation; queue limit then fail-closed |
| Same request ID from two servers | Daemon ID is generated by us, not taken from Claude's ID |
| Responses arriving out of order | Shim maps by original JSON-RPC id, never by order |
| Batch (JSON array) requests | Split, decide each item separately, reassemble the reply array |

### 6.2 Decision edge cases
| Case | Handling |
|---|---|
| User ignores popup | Timeout (default 60 s, configurable) → auto-Block with message "timed out" |
| Client cancels (`notifications/cancelled`) while pending | Remove card, resolve as cancelled, do not forward |
| Client disconnects/dies while pending | Daemon drops the card when the shim connection closes |
| Accidental click/keypress when popup steals focus | Buttons inert for ~600 ms after appearing; Enter does not trigger Allow; Esc = Block |
| Double-click / duplicate decision | First decision wins; later ones ignored |
| User closes popup with X | Treated as Block |
| Daemon asks, user decides after shim already gave up | Late decision discarded and logged |

### 6.3 Failure edge cases
| Case | Handling |
|---|---|
| Daemon not running | Shim tries to auto-start it once; if still unreachable → fail-closed (block with clear message). Configurable `fail_mode=closed|open`, default closed |
| Daemon crashes mid-request | Shim detects connection loss → block |
| Real server crashes/exits | Shim returns JSON-RPC errors for all in-flight requests, then exits with the same code so the client sees the failure |
| Real server hangs | Pass through client's own timeouts; shim can enforce an upstream timeout |
| Shim killed by the client (stdin closes) | Terminate child server, close IPC, clean up pending records |
| Zombie child processes | Child tied to shim lifetime (Windows Job Object); always killed on shim exit |
| Malformed JSON / partial line / huge message | Never crash; unparseable data from Claude → pass through only if it is not a recognisable call (documented), log it; size cap with safe handling |
| Binary or non-UTF-8 output on stdout | Pass bytes through untouched |
| Server writes logs to stdout (violates protocol) | Pass through; never break framing |
| Server writes to stderr | Pass through to our stderr untouched |
| Unicode / emoji / very long arguments | UTF-8 safe; popup truncates, full data in log |
| Daemon port already in use | Pick next port / use a lock file; single-instance guard |
| Two daemons started | Second one detects the lock and exits |
| Stale token file | Regenerated on daemon start; shims re-read on reconnect |
| IPC auth failure | Reject and log; fail-closed |
| Popup display failure (no display, locked workstation) | Treat as undecidable → fail-closed, log reason |
| Windows lock screen / sleep during pending request | Timeout applies; request blocked on resume if expired |
| Multiple monitors / DPI scaling | Show on the monitor with the active window / cursor; scaling-aware |
| Fullscreen apps (games, video) | Best effort topmost; plus tray notification and taskbar flash |

### 6.4 Protocol edge cases
| Case | Handling |
|---|---|
| Notifications (no id) such as `notifications/cancelled` | Cannot be blocked/answered; pass through (documented) |
| `tools/call` using string vs number ids | Preserve the original id type exactly in denial replies |
| Server-initiated requests (sampling, elicitation, roots) | Out of scope for approval by default; passed through; optionally logged |
| Progress/streaming notifications | Pass through untouched |
| `resources/read`, `prompts/get` | Intercepted if enabled in config (default: tools only, because the requirement targets tool invocation; option exists) |
| Protocol version differences | Shim does not rewrite messages, so version-agnostic |
| Blocked call must not leave the real server in a bad state | The server never saw the request, so no cleanup needed |
| Claude retries after a block | Each retry is a new request and a new popup; optional short "recently blocked" hint |

### 6.5 Config / installer edge cases
| Case | Handling |
|---|---|
| Config file missing, empty, or invalid JSON | Report clearly, do not write, do not crash |
| Config edited by Claude app while installer runs | Read-modify-write with re-check; atomic write (temp file + rename) |
| Server already wrapped | Detected, no double-wrap |
| Server entry types: stdio command, `npx`, `uvx`, `python`, Windows `.cmd`/`.bat`, paths with spaces, env vars, `cwd` | Preserved exactly; tested |
| Remote/URL-based entries | Not wrapped by shim; offered to the gateway flow |
| Claude updates and overwrites config | Watcher/`doctor` command detects drift and re-wraps |
| Uninstall | Restores original entries from backup; verifies result |
| Claude Desktop must restart to read new config | Installer says so; optional helper to restart |

---

## 7. Security

1. **Fail-closed** by default everywhere.
2. **Local IPC only**: bind to `127.0.0.1`, random per-install auth token in a file readable only by the current user. No remote access to the daemon.
3. **Gateway hardening**: unguessable secret path/token in the URL, HTTPS via tunnel, rate limiting, request size limits, allow-list of upstreams, never expose the daemon through the tunnel.
4. **No secrets in logs/popup**: mask values for keys like `token`, `password`, `api_key`, `authorization`, bearer strings; full redaction option.
5. **Log file permissions** restricted to the user; log rotation; option to disable arguments logging.
6. **No shell injection**: child commands launched as an argument list, never through a shell string.
7. **Tamper awareness**: `doctor` reports if a wrapped entry was changed back to the original (monitor bypassed) — shown as a warning.
8. **Popup content safety**: parameters rendered as plain text only; no markup execution; long text truncated, to prevent a malicious server/tool from spoofing the Allow/Block UI.
9. **No auto-allow by default**: "remember this choice" is opt-in per server+tool, expires, and is easy to revoke.
10. **Honest limits** documented: a user with admin rights can bypass the monitor; Claude Web direct connectors are not covered.

---

## 8. Reliability

- Daemon: tray icon, single instance, optional start at login, graceful shutdown (resolves all pending as blocked, closes sockets).
- Shim: small, minimal dependencies, fast startup (it is on the path of every server start).
- Structured logging with levels; one log per component; clear error messages shown in the popup when something degrades.
- All exceptions in the interception path caught and converted to a block + log, never an unhandled crash that could let a call through.
- Unit tests for protocol parsing; integration tests with a fake MCP server; chaos tests (kill daemon, kill server, flood).

---

## 9. Backups and interference with other sessions

### 9.1 Will this interfere with other sessions? — **Yes, it can.**
Editing a Claude config file is **global**, not per session:
- `claude_desktop_config.json` affects **every** Claude Desktop chat after restart.
- `~/.claude.json` (Claude Code user scope) affects **every** Claude Code session on this machine, including this one and any other open window.
- A wrapped server affects every session that uses that server. If the daemon is not running and fail mode is closed, those servers' tools are blocked everywhere.
- The shim adds slight latency and an extra process per server.
- A bug in the shim could break that server for all sessions.

### 9.2 How we limit the risk
1. **Opt-in per server**: nothing is wrapped unless the owner picks it.
2. **Test with a fake server first** (our own dummy MCP server), then one real low-risk server.
3. **Prefer project-scoped config for Claude Code** (`.mcp.json` inside a test project) so other sessions are untouched.
4. **Never touch this working session's own MCP servers** (intranet, Slack, Gmail, Figma, etc.) during development.
5. **Backups before every config write**, stored in `backups/` (timestamped, never overwritten), plus a one-command restore: `installer restore`.
6. **Dry-run mode** prints the diff and writes nothing.
7. **Emergency off switch**: environment variable / config flag `MCP_MONITOR_DISABLE=1` makes shims pure pass-through (no popups) so a broken monitor never locks the user out.
8. **Manual recovery instructions** in README (copy the backup file back, restart Claude).

### 9.3 Backup folder (in the current project folder)
```
backups/
  plan.backup.md            copy of this plan (kept in sync by hand when the plan changes)
  plan-b-fallback.md        fallback plan if config rewriting proves too disruptive
  config/                   created by the installer at run time:
    claude_desktop_config.<timestamp>.json
    claude_code_mcp.<timestamp>.json
```
Rules: never overwrite an existing backup; keep the original-first backup forever; verify the backup is valid JSON before modifying the original.

---

## 10. Build order and timeline (deadline Fri 20 Nov EOD IST; today is Wed 18 Nov)

| Phase | Work | Target |
|---|---|---|
| 0 | Owner approves plan | Wed 18 Nov |
| 1 | Protocol layer, fake MCP server, stdio shim, daemon with queue, basic Tkinter popup, IPC | Wed night |
| 2 | Test with Claude Code app on a project-scoped `.mcp.json` (allow, block, concurrent, timeout, crash cases) | Thu AM |
| 3 | Installer with backup/restore/dry-run; test on Claude Desktop with one real server | Thu PM |
| 4 | Claude Web gateway + tunnel; test end-to-end from claude.ai | Thu night / Fri AM |
| 5 | Hardening, tests, README, architecture doc | Fri midday |
| 6 | Record demo video (allow + block, concurrent, Desktop and Web), upload, submit form | Fri afternoon, buffer before EOD |

The timeline is tight. If Phase 4 slips, the plan-b file describes what to ship.

---

## 11. Documentation and deliverables checklist

- [ ] Complete Python source in GitHub repo
- [ ] README: prerequisites, install, configure, run, uninstall/restore, troubleshooting
- [ ] Architecture explanation: components, diagrams, interception approach, limits
- [ ] Security notes and known limitations
- [ ] Test instructions and results
- [ ] Demo video: (1) a request is approved and succeeds, (2) a request is blocked and Claude reports the denial, (3) two simultaneous requests, (4) Claude Web through the gateway
- [ ] Screenshots of the popup in the README
- [ ] CV + repo link + video link submitted via the form

---

## 12. Test plan (summary)

- **Unit**: JSON-RPC parsing, method classification, batch handling, id preservation, secret masking, config rewrite/restore.
- **Integration**: fake MCP server with tools: `echo`, `slow`, `crash`, `big_output`, `bad_json`; shim + daemon run together; scripted decisions.
- **Scenarios**: allow, block, timeout, cancel, client disconnect, daemon down, daemon crash mid-request, server crash, 20 concurrent calls, huge arguments, unicode, Windows paths with spaces.
- **Manual**: popup above fullscreen/other apps, multi-monitor, lock/unlock, real Claude Code and Desktop sessions, real Claude Web via gateway.

---

## 13. Risks and open questions

| Risk | Mitigation |
|---|---|
| Claude Desktop remote connectors and `.mcpb` extensions may not be wrappable via config | Investigate early; document support matrix |
| Claude Web needs a public tunnel; stability and domain changes | Use a stable tunnel URL; document the setup; plan-b if blocked |
| Claude Web may show its own approval UI too | Document that ours is an additional gate |
| Windows focus-stealing rules limit "always on top" behaviour | Topmost flag + taskbar flash + tray notification fallback |
| Tight deadline | Phased build with a working local demo by end of Phase 3 |
| Terms of service: modifying only config files and proxying our own servers' traffic is fine; no app injection is used | Keep it that way |

Open questions for the owner (non-blocking):
1. Rename the assignment `requirements.txt`, or keep it and use `pyproject.toml` for dependencies? (plan assumes keep)
2. Do you want "remember my choice" in v1, or skip it?
3. Which real MCP server should we use for the Desktop demo (filesystem server recommended)?
