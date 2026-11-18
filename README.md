# MCP Communication Monitor

A Python app that watches the tool calls Claude makes to MCP servers and **asks you Allow or Block for each one**, in a popup that appears on top of your other windows. A blocked call is never executed, and Claude gets a clear refusal back.

Works with **Claude Desktop**, **Claude Code** and **Claude Web (claude.ai)**.

![Approval popup](docs/images/popup.png)

**Demo video:** _link added on submission_

## What it does

* **Holds every tool call** until you decide. Allow forwards the original request; Block answers Claude directly, and the real tool never sees it.
* **Shows what is being asked:** who is asking, which MCP server, which tool, and the parameters (secrets masked).
* **Handles many requests at once:** one popup per request, decided independently.
* **Fails safe:** if anything goes wrong (monitor not running, no answer, crash), the request is blocked.
* **Keeps an audit log** of every request and decision.

## Quick start

Requires Python 3.10+ (developed on Windows 11).

```bash
git clone https://github.com/Ashish558/MCP-Communication-Monitor.git
cd MCP-Communication-Monitor
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[tray,gateway]"

.venv\Scripts\python scripts\popup_demo.py     # try it: three simultaneous popups, no Claude needed
.venv\Scripts\python -m pytest -q              # run the 73 tests
```

To use it with Claude, follow **[docs/setup.md](docs/setup.md)**.

## What is intercepted

| What Claude uses | Intercepted? | How |
|---|---|---|
| Local MCP server in Claude Desktop | Yes, a real block | A shim wraps the server in Claude's config |
| Local MCP server in Claude Code | Yes, a real block | Same shim |
| Your own remote MCP server, used from Claude Web | Yes, a real block | Gateway and a tunnel; the tunnel address is the connector |
| Connector added directly in claude.ai (Figma, Gmail, Slack...) | Yes, **best effort**, in the browser only | A [browser extension](extension/README.md) holds claude.ai's own "Allow once" click until you answer in our popup (verified with Figma in Edge) |
| The same connectors inside Claude Desktop / Claude Code | **No** | Run through Anthropic's servers; our shim and gateway are not in their path |

Claude Web calls tools from Anthropic's cloud, not from your PC, so a program on your PC can only stand in the path where the tool's address is its own (the gateway), or act on the approval click that happens in your browser (the extension). Details and the full list of limits: [docs/edge-cases.md](docs/edge-cases.md).

## How it works, in short

```
Claude Desktop / Code --> shim --> real MCP server
Claude Web --> tunnel --> gateway --> real MCP server
Browser extension --> bridge
        shim / gateway / bridge --> daemon (queue, popup, audit log) --> you: Allow or Block
```

Full explanation with diagrams: [docs/architecture.md](docs/architecture.md).

## Documentation

| Document | Contents |
|---|---|
| [docs/setup.md](docs/setup.md) | Install and setup for each kind of tool, undo and recovery, troubleshooting |
| [docs/configuration.md](docs/configuration.md) | Settings, environment variables, command reference, log files |
| [docs/architecture.md](docs/architecture.md) | How interception works, components, concurrency, failure handling, design decisions |
| [docs/security.md](docs/security.md) | Security controls, trust boundaries and limits |
| [docs/edge-cases.md](docs/edge-cases.md) | Every edge case, what happens, and the test or check that proves it |
| [docs/testing.md](docs/testing.md) | How to run the tests, what they cover, what was verified by hand |
| [extension/README.md](extension/README.md) | The claude.ai browser extension |

## Project layout

```
src/mcp_monitor/
  protocol.py    JSON-RPC helpers, secret masking, safe display text
  gatekeeper.py  one "ask the daemon, apply the failure rule" routine
  shim.py        stdio proxy in front of one MCP server
  gateway/       HTTP gateway for Claude Web
  bridge.py      local endpoint the browser extension asks for decisions
  ipc.py         proxy <-> daemon channel (127.0.0.1 + token)
  daemon/        decision engine (queue, timeouts, cancel), Tk popups, tray, audit log
  installer.py   backup-first config wrapping and unwrapping
  cli.py         python -m mcp_monitor <command>
extension/       claude.ai browser extension (JavaScript)
tests/           73 tests and a fake MCP server
scripts/         popup demo, screenshot helper
docs/            documentation
```

## Known limits

* Claude.ai connectors (Figma, Gmail...) used inside Claude Desktop or Claude Code work normally but are not monitored: they go through Anthropic's servers, not through our shim or gateway. Interception there was not attempted.
* The browser extension works only when claude.ai shows its own approval prompt; it is best effort.
* Developed and tested on Windows; popups open on the primary monitor; the daemon does not start at login.
* The browser extension is JavaScript, because browser extensions cannot be written in Python. Everything else is Python.

## License

[MIT](LICENSE)
