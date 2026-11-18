"""Claude Code PreToolUse hook: the same Allow/Block gate for tools the shim cannot see.

Claude Code runs a ``PreToolUse`` hook before *every* tool call, including MCP tools
(named ``mcp__<server>__<tool>``) and connectors added at claude.ai. This command is
registered as such a hook (``mcp-monitor hook-install``). It reads the call from stdin,
asks the daemon for a decision (same popup, same audit log), and answers Claude Code:

* Allow -> exit 0 with ``permissionDecision: "allow"``.
* Block -> exit 2 and a reason on stderr. Exit 2 is the documented way to block a call.
* Anything unexpected (bad input, daemon down, timeout, crash) -> exit 2, so it blocks.
  Claude Code lets a call through when a hook fails with any *other* exit code, which is why
  every failure path here returns 2 and the installer also sets ``"onFailure": "block"``.

Non-MCP tools (Bash, Edit, ...) are not this app's business: no output, exit 0.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from typing import Callable

from .config import Settings
from .gatekeeper import Gatekeeper
from .ipc import DaemonClient, new_ref

log = logging.getLogger("mcp_monitor.hook")

CLIENT_LABEL = "claude-code (hook)"


@dataclass
class HookResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""


def split_mcp_tool(tool_name: str) -> tuple[str, str] | None:
    """``mcp__<server>__<tool>`` -> ``(server, tool)``; ``None`` for non-MCP tools."""
    if not tool_name.startswith("mcp__"):
        return None
    rest = tool_name[len("mcp__"):]
    server, sep, tool = rest.partition("__")
    if not sep or not server or not tool:
        return rest, rest
    return server, tool


def _deny(reason: str) -> HookResult:
    return HookResult(2, "", f"Blocked by MCP Monitor. {reason}".strip())


def _allow() -> HookResult:
    return HookResult(0, json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "permissionDecisionReason": "Allowed in MCP Monitor",
    }}))


GateFactory = Callable[[str], Gatekeeper]


def handle_payload(payload: object, settings: Settings, gate_factory: GateFactory | None = None) -> HookResult:
    """Decide one hook call. Never raises: every failure becomes a block."""
    try:
        if settings.disabled:                                     # emergency off switch: no opinion
            return HookResult(0)
        if not isinstance(payload, dict):
            return _deny("The hook input was not readable.")
        if payload.get("hook_event_name") not in (None, "PreToolUse"):
            return HookResult(0)
        parsed = split_mcp_tool(str(payload.get("tool_name", "")))
        if parsed is None:
            return HookResult(0)                                  # not an MCP tool
        server_id, tool = parsed
        mcp = payload.get("mcp_server")
        server = mcp["name"] if isinstance(mcp, dict) and isinstance(mcp.get("name"), str) else server_id
        if server in settings.hook_skip_servers or server_id in settings.hook_skip_servers:
            return HookResult(0)                                  # e.g. already covered by a shim
        arguments = payload.get("tool_input")
        if not isinstance(arguments, dict):
            arguments = {} if arguments is None else {"input": arguments}
        msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": tool, "arguments": arguments}}
        make = gate_factory or (lambda name: Gatekeeper(settings, DaemonClient(settings), name))
        decision = make(server).ask(new_ref(), msg, CLIENT_LABEL)
    except Exception:                                             # noqa: BLE001 - fail closed
        log.exception("hook failed")
        return _deny("Internal monitor error.")
    log.info("hook decision %s for %s/%s", decision.verdict, server, tool)
    return _allow() if decision.verdict == "allow" else _deny(decision.reason)


def run_hook() -> int:
    """Entry point for ``python -m mcp_monitor hook``: stdin JSON in, exit code out."""
    from .shim import configure_logging

    settings = Settings.load()
    configure_logging(settings.log_level, "hook.log")
    try:
        payload = json.loads(sys.stdin.read())
    except ValueError:
        payload = None
    result = handle_payload(payload, settings)
    if result.stdout:
        sys.stdout.write(result.stdout)
        sys.stdout.flush()
    if result.stderr:
        sys.stderr.write(result.stderr)
        sys.stderr.flush()
    return result.exit_code
