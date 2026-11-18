# Security model

## What the app protects

It lets a person decide, one request at a time, whether an AI client may run an MCP tool. The goals are:

* A tool call never runs without the user's explicit Allow (or an explicit fail-open setting).
* The popup shows what is really being asked, and cannot be tricked into showing something else.
* Nothing outside the user's PC can reach the control path.

## Controls

| Area | Control |
|---|---|
| **Fail-safe default** | Any doubt means Block: daemon down, no answer, crash, unexpected error. `fail_mode: "open"` exists but is never the default. |
| **Local channel** | The daemon listens on 127.0.0.1 only. Every connection must present a random per-install token stored in a per-user file. A connection that does not authenticate is dropped. Clients can only *ask* for a decision; only the popup can answer. |
| **Popup honesty** | Parameters are shown as plain text. Control, invisible and right-to-left override characters, and icon-font glyphs, are escaped so a tool cannot spoof the dialog. Secrets are masked (keys such as `password`, `token`, `api_key`; values shaped like bearer tokens, `sk-...`, GitHub tokens, AWS keys, Slack tokens). |
| **Accidental approval** | Buttons ignore clicks for 0.6 s after the popup appears, Enter does nothing, and Esc or closing the window means Block. |
| **Gateway** | Secret token in the URL path (wrong paths get a bare 404, compared in constant time); requests with a browser `Origin` other than claude.ai or claude.com are refused; 10 MB body limit; in-flight cap; JSON-RPC batches are refused rather than half-handled; the access log is disabled because it would contain the token; binds to 127.0.0.1 and the daemon port is never exposed through the tunnel. Even with a leaked URL, each `tools/call` still needs a click. |
| **Browser bridge and extension** | The bridge requires the secret token, binds to 127.0.0.1, and refuses web-page origins (http/https). The extension runs only on claude.ai and claude.com, only its background worker talks to localhost, and any error makes it click Decline. |
| **Claude Code hook** | Every failure (unreadable input, daemon down, timeout, error) exits with code 2, which Claude Code treats as a block. The installer sets `onFailure: "block"` (Claude Code 2.1.295+) so a hook that cannot start also blocks. Parameters are masked the same way as everywhere else. |
| **No shell** | Server commands are started as argument lists, never through a shell string. |
| **Safe config changes** | Backup before every change (the first backup is never overwritten), dry run, atomic write, validation of the result, and an exact undo. |
| **Process hygiene** | On Windows a job object kills the real MCP server if the shim dies, so no orphan keeps running. |
| **Audit trail** | Every request and decision is logged with rotation. Set `log_arguments: false` to keep parameters out of the log. |

## Trust boundaries and limits

* **Same-user processes.** Another program running as the same user can read the token files and connect to the daemon. It could make popups appear, but it cannot approve anything, because decisions only come from the popup.
* **Administrators and the user themselves** can bypass the monitor by editing Claude's config back or setting `MCP_MONITOR_DISABLE=1`. `doctor` reports servers that are not monitored.
* **Tokens at rest** are plain files in the user's data folder (`gateway.json`, `bridge.json`, `daemon.json`). Rotate a token by deleting its file or with `--rotate-token`.
* **Tunnel providers** (Cloudflare, ngrok) can see the traffic that passes through the tunnel. Do not put secrets in tools you expose through a demo tunnel.
* **The extension reads claude.ai's own prompt.** It sees the parameters as displayed, which may differ from the raw request, and a redesign of that prompt can make it stop working (it then fails safe: nothing is allowed that you did not click).
* **Tools the monitor does not see:** claude.ai connectors (Figma, Gmail...) in the Claude Desktop chat tab are not covered: no shim, gateway or hook is in their path, and interception there was not attempted.
* **The hook depends on Claude Code.** It is a gate Claude Code consults, so it is only as strong as that feature: Claude Code's own deny and ask rules still apply, an "allow" from the hook skips Claude Code's own prompt (the user already approved in our popup), hooks live in settings files that the user can edit, and on Claude Code older than 2.1.295 a hook that fails to start (for example, Python missing) lets the call run. Tested on Claude Code 2.1.302.
* **Audit log contents:** parameters are masked for secrets only by key name and value shape. Personal data in ordinary text is not detected; use `log_arguments: false` if that matters.

See also: [edge cases](edge-cases.md) and [architecture](architecture.md).
