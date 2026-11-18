"""JSON-RPC / MCP message helpers.

Pure functions only (no I/O) so they are easy to test.

MCP messages are JSON-RPC 2.0, sent one per line over stdio:
  request       {"jsonrpc":"2.0","id":1,"method":"tools/call","params":{...}}
  notification  same but without "id" (no reply expected, cannot be blocked)
  response      {"jsonrpc":"2.0","id":1,"result":{...}} or "error":{...}
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

BLOCKED_ERROR_CODE = -32001     # request refused by the user / monitor
INTERNAL_ERROR_CODE = -32603    # JSON-RPC "internal error" (server died, etc.)
MAX_DEPTH = 20

_SECRET_KEY = re.compile(
    r"password|passwd|secret|token|api[_-]?key|authorization|credential|cookie|"
    r"private[_-]?key|access[_-]?key|bearer",
    re.IGNORECASE,
)
_NOT_SECRET_KEY = re.compile(r"max_?tokens?|tokens?_?(count|limit|usage)|num_?tokens?", re.IGNORECASE)
_SECRET_VALUES = [
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
]


# --------------------------------------------------------------------------- parsing
def parse_line(line: bytes) -> Any | None:
    """Decode one line of JSON. Returns ``None`` if it is not valid JSON."""
    try:
        return json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def is_request(msg: Any) -> bool:
    """A JSON-RPC request: has a ``method`` and a non-null ``id`` (so it expects a reply)."""
    return (
        isinstance(msg, dict)
        and isinstance(msg.get("method"), str)
        and msg.get("id") is not None
    )


def is_response(msg: Any) -> bool:
    return (
        isinstance(msg, dict)
        and "method" not in msg
        and msg.get("id") is not None
        and ("result" in msg or "error" in msg)
    )


def id_key(rpc_id: Any) -> str:
    """Hashable key for a JSON-RPC id that keeps ``1`` and ``"1"`` distinct."""
    return json.dumps(rpc_id, sort_keys=True)


@dataclass(frozen=True)
class CallInfo:
    method: str
    name: str
    arguments: Any


def describe(msg: dict) -> CallInfo:
    """Extract what the user needs to see: method, tool/resource name, parameters."""
    method = msg.get("method", "")
    params = msg.get("params")
    if not isinstance(params, dict):
        params = {}
    if method == "tools/call":
        return CallInfo(method, str(params.get("name", "<unknown tool>")), params.get("arguments", {}))
    if method == "resources/read":
        return CallInfo(method, str(params.get("uri", "<unknown resource>")), {})
    if method == "prompts/get":
        return CallInfo(method, str(params.get("name", "<unknown prompt>")), params.get("arguments", {}))
    return CallInfo(method, method, params)


def client_name_from_initialize(msg: dict) -> str | None:
    params = msg.get("params")
    if isinstance(params, dict):
        info = params.get("clientInfo")
        if isinstance(info, dict) and isinstance(info.get("name"), str):
            version = info.get("version")
            return f"{info['name']} {version}" if isinstance(version, str) else info["name"]
    return None


# --------------------------------------------------------------------------- replies
def denial_response(msg: dict, reason: str) -> dict:
    """Reply sent to Claude instead of running a blocked request.

    For ``tools/call`` this is a normal tool result flagged ``isError`` so the
    model reads the explanation and can tell the user. Other methods get a
    JSON-RPC error.
    """
    text = f"Blocked by the user via MCP Monitor. {reason}".strip()
    if msg.get("method") == "tools/call":
        return {
            "jsonrpc": "2.0",
            "id": msg["id"],
            "result": {"content": [{"type": "text", "text": text}], "isError": True},
        }
    return error_response(msg["id"], BLOCKED_ERROR_CODE, text)


def error_response(rpc_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def encode(msg: Any) -> bytes:
    """Serialize to one newline-terminated UTF-8 line (compact, as MCP stdio requires)."""
    return json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


# --------------------------------------------------------------------------- display safety
def redact(value: Any, _depth: int = 0) -> Any:
    """Mask likely secrets (by key name and by value shape) before showing or logging."""
    if _depth > MAX_DEPTH:
        return "[depth limit]"
    if isinstance(value, dict):
        out = {}
        for key, val in value.items():
            if isinstance(key, str) and _SECRET_KEY.search(key) and not _NOT_SECRET_KEY.search(key):
                out[key] = "***"
            else:
                out[key] = redact(val, _depth + 1)
        return out
    if isinstance(value, list):
        return [redact(v, _depth + 1) for v in value]
    if isinstance(value, str):
        for pattern in _SECRET_VALUES:
            value = pattern.sub("***", value)
        return value
    return value


def sanitize_text(text: str) -> str:
    """Make text safe to show in the popup.

    Control and invisible formatting characters (e.g. the right-to-left override
    U+202E) are shown as escapes so a malicious tool cannot spoof the dialog.
    """
    out = []
    for ch in text:
        if ch in "\n\t":
            out.append(ch)
        elif unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp"):
            out.append(f"\\u{ord(ch):04x}" if ord(ch) <= 0xFFFF else f"\\U{ord(ch):08x}")
        else:
            out.append(ch)
    return "".join(out)


def preview(value: Any, max_chars: int) -> tuple[str, int]:
    """Redacted, sanitized, length-limited text of ``value`` plus its original size."""
    try:
        text = json.dumps(redact(value), ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    size = len(text)
    if size > max_chars:
        text = text[:max_chars] + f"\n… [truncated, {size - max_chars} more characters]"
    return sanitize_text(text), size
