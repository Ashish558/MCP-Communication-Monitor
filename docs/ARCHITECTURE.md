# Architecture and approach

This document explains how MCP Monitor intercepts MCP traffic, why it was built this way, and how it behaves when things go wrong.

## 1. The problem

When Claude uses a tool (read a file, search, send a message) it sends a small JSON request to an **MCP server**. By default nothing asks the user before the request runs. The goal is to hold **each individual request**, show it to the user, and run it only after the user clicks **Allow**. If the user clicks **Block**, the request must not run and Claude must get a clear refusal.

The difficulty: Claude Desktop, Claude Code and Claude Web do not reach MCP servers in the same way.

| Client | How the MCP call travels | Where we can stand in the middle |
|---|---|---|
| Claude Desktop / Claude Code, local server | Client starts the server as a child process and talks to it over **stdin/stdout** | On the user's PC, between the client and the child process |
| Claude Web (claude.ai), connector | **Anthropic's servers** call the MCP server's URL over HTTPS | Only at the URL itself, which therefore has to be ours |

## 2. Approach: a proxy in the path

The only reliable way to *pause and block* a request is to be the program the request passes through. So there are two small proxies that share one decision engine:

```mermaid
flowchart LR
    subgraph PC["User's PC"]
        CD["Claude Desktop / Claude Code"] -- "stdio" --> SH["Shim (one per MCP server)"]
        SH -- "stdio" --> RS["Real MCP server"]
        GW["Gateway (HTTP, 127.0.0.1)"] --> RS2["Real MCP server<br/>(stdio child or URL)"]
        SH -- "local socket + token" --> D
        GW -- "local socket + token" --> D
        D["Daemon<br/>queue - popup - audit log"] --> U(("User:<br/>Allow / Block"))
    end
    CW["Claude Web (claude.ai)<br/>Anthropic's cloud"] -- "HTTPS via tunnel" --> GW
```

* **Shim**: Claude is told (in its config file) to start the shim instead of the real server. The shim starts the real server itself and relays every message in both directions.
* **Gateway**: a small web server that speaks MCP over HTTP. A tunnel (Cloudflare Quick Tunnel or ngrok) gives it a public HTTPS address, which the user adds as a custom connector in claude.ai.
* **Daemon**: one background app that owns the popup, the queue of waiting requests, and the audit log. Shims and the gateway ask it for every decision.

### A third door for connectors added directly in claude.ai (best effort)

A connector added in claude.ai is called by Anthropic's cloud, so neither the shim nor the gateway sees it. One hook remains: claude.ai shows "Claude wants to use X from Y: Decline / Always allow / Allow once" in the browser, and the tool runs only after the browser sends that approval. A small **browser extension** holds the approving click, sends the tool name, connector and displayed parameters to a local **bridge** (`bridge.py`, which uses the same gatekeeper and popup), and then clicks "Allow once" or "Decline" for the user. "Always allow" is converted into "Allow once" so every call stays gated.

This is deliberately labelled best effort: it only works when claude.ai shows its prompt, it reads the parameters as displayed (not the raw request), it depends on the prompt's layout, and it covers the browser only. It fails closed (any error means Decline). Details: [extension/README.md](../extension/README.md).

### Alternatives that were rejected

| Idea | Why not |
|---|---|
| Sniff network traffic | MCP over HTTPS is encrypted, and sniffing can watch but never block. |
| Inject code into the Claude app (Electron debugging, DLL hooks) | Breaks on every update and may violate the app's terms. |
| Browser extension on claude.ai | The tool call runs on Anthropic's servers, so an extension could see it but not stop it. |
| Watch for new processes | Shows that a server started, not what it was asked to do. |

## 3. What happens to one request

```mermaid
sequenceDiagram
    participant C as Claude
    participant S as Shim / Gateway
    participant D as Daemon
    participant U as User
    participant R as Real MCP server
    C->>S: tools/call (tool + parameters)
    S->>D: "may this run?" (redacted preview)
    D->>U: popup (server, tool, parameters)
    alt Allow
        U->>D: Allow
        D->>S: allow
        S->>R: original request, unchanged
        R->>S: result
        S->>C: result
    else Block / timeout / error
        U->>D: Block
        D->>S: block
        S->>C: "Blocked by MCP Monitor" (the server never sees the request)
    end
```

Details that matter:

* Only requests in `intercept_methods` (default: `tools/call`) are held. Everything else (`initialize`, `tools/list`, notifications, responses) passes through untouched, so the proxy works with any MCP protocol version and never rewrites a message.
* Allowed requests are forwarded as the **original bytes**, not a re-encoded copy.
* A block is a normal tool result with `isError: true` and a readable message, so the model can explain the refusal to the user. Non-tool methods get a JSON-RPC error instead.
* The popup shows the client, MCP server, tool and parameters. The server name comes from the config key (shim) or the gateway's `--name`; tool and parameters come from the request itself; the client name comes from the MCP `initialize` handshake.

## 4. Components

| Module | Responsibility |
|---|---|
| `protocol.py` | Pure functions: classify messages, build denials, mask secrets, make text safe to display. |
| `gatekeeper.py` | The single "ask the daemon, apply the failure rule" routine used by both proxies. |
| `shim.py` | Stdio proxy: holds/forwards/denies, tracks in-flight requests, handles server crashes, cleans up the child. |
| `gateway/` | HTTP proxy for Claude Web: session handling, upstream (stdio or HTTP), security checks. |
| `ipc.py` | Local channel between proxies and daemon (127.0.0.1, random port, secret token). |
| `daemon/broker.py` | Decision engine: pending queue, timeouts, cancellation, cleanup. UI-independent. |
| `daemon/ui.py` | Tkinter popups; `daemon/tray.py` optional tray icon; `daemon/audit.py` JSON-lines log. |
| `installer.py` | Safely edits Claude config files: backup first, dry-run, atomic write, exact undo. |
| `jobobject.py` | Windows job object so a killed shim cannot leave an orphaned MCP server. |

The broker only talks to a small `Presenter` interface (`show`, `close`, `pending_changed`). The tests use a rule-driven presenter instead of the popup, which is how the full request path is tested without a human.

## 5. Concurrency

* Every request gets a unique id and its own popup card. Decisions are independent and the first answer wins.
* The daemon runs an asyncio loop on a background thread; the Tk window runs on the main thread. They communicate through a thread-safe queue and `Broker.resolve`, which is safe to call from any thread.
* At most 3 popups are on screen (configurable); the rest wait in a queue and open as earlier ones are answered. More than `max_pending` (100) at once are blocked immediately.
* Held requests never block other traffic: `tools/list`, pings and other calls keep flowing while a popup is open.
* JSON-RPC ids are matched exactly (`1` and `"1"` are different), and two shims using the same id never collide because the daemon assigns its own ids.

## 6. Failure handling (fail closed)

The rule: **if the monitor cannot be sure the user said yes, the request is blocked.** (`fail_mode: "open"` exists but is never the default.)

| Situation | Result |
|---|---|
| Daemon not running | Shim tries to start it; if that fails, the request is blocked with an explanation. Other traffic still works. |
| Daemon crashes or is stopped while a popup is open | Every waiting request is blocked. |
| User ignores the popup | Blocked after `decision_timeout` (60 s default). |
| User closes the popup window or presses Esc | Block. Buttons ignore clicks for 0.6 s and Enter does nothing, so a stray keypress cannot approve. |
| Claude cancels or hangs up while waiting | Popup is dismissed and the request is withdrawn; nothing runs. |
| Real server crashes | Every in-flight request gets an error reply; the shim exits with the server's exit code. |
| Claude closes the shim | Real server is shut down (and killed if needed); a Windows job object covers hard kills. |
| Unexpected error inside the monitor | Caught, logged, and treated as Block. |
| Malformed data | A line that is not valid JSON cannot be recognised as a tool call, so it is passed on (the real server rejects it) and logged. It never crashes the proxy. |
| Emergency | `MCP_MONITOR_DISABLE=1` turns shims into plain pass-through. |

## 7. Security

* **Local channel**: the daemon listens on 127.0.0.1 only, and every connection must present a random token stored in a per-user file.
* **Gateway**: secret token in the URL path (wrong paths give a bare 404); browser `Origin` checks reject anything but claude.ai / claude.com; 10 MB body limit; in-flight cap; access log disabled because it would contain the token; the daemon port is never exposed through the tunnel. Even if the URL leaks, each `tools/call` still needs a click.
* **Popup honesty**: parameters are shown as plain text; control and invisible/bidirectional characters are escaped so a tool cannot spoof the dialog. Secrets (keys named `password`, `token`, `api_key`…, and values shaped like bearer tokens, `sk-…`, GitHub tokens, AWS keys) are masked in the popup and the audit log.
* **No shell**: server commands are launched as argument lists, never through a shell string.
* **Safe config changes**: backup before every change (the first backup is never overwritten), dry-run, atomic write, validation of the result.
* **Audit log**: every request and decision is written to `audit.jsonl` with rotation.

## 8. Design decisions and trade-offs

* **Proxy over hooking.** More setup (a config change) but it is stable, readable, and can really block.
* **One decision engine, two doors.** Adding the gateway needed no change to the popup or the queue.
* **Tkinter.** No extra dependency and good enough for a topmost dialog; the UI sits behind the `Presenter` interface so it can be swapped.
* **Standard library for the core.** The shim has no third-party dependencies (it starts on every server launch). Only the gateway needs `aiohttp`; the tray icon needs `pystray` + `Pillow` and is optional.
* **Block replies are results, not protocol errors**, so the model sees why and can tell the user.

## 9. Known limitations

* Claude Web is covered with a hard block only for connectors routed through the gateway. Connectors added directly in claude.ai are covered by the browser extension on a best-effort basis; the same connectors inside Claude Desktop or Claude Code (run by Anthropic's cloud) cannot be intercepted.
* The gateway does not support JSON-RPC batches (refused with an error) or server-initiated messages / GET streams.
* Notifications have no reply, so they cannot be blocked.
* Popups open on the primary monitor.
* A user with administrator rights can bypass the monitor by editing the config back (`doctor` reports unmonitored servers).
* Quick Tunnel addresses change on every restart.

## 10. Testing

* 68 automated tests: protocol helpers, installer (backup, dry-run, undo, corrupt files), the shim end to end against a fake MCP server (allow, block, 20 concurrent calls, timeout, cancel, daemon down, daemon stopped mid-request, server crash, batches, large and unicode data, disable switch), the gateway (tokens, origins, sessions, concurrency, hang-ups, HTTP upstream including streamed replies), and the browser bridge (token, origin checks, fail closed). The extension's click-holding logic was exercised on a mock of claude.ai's approval prompt.
* Manual end-to-end checks: live Claude Code session, Claude Desktop, Claude Web through a Cloudflare tunnel, the Figma connector in claude.ai through the browser extension (Edge), daemon autostart, real popup timeout.
