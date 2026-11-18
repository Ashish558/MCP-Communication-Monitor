"""Tiny stdio MCP server used by the tests and by manual demos.

Tools: echo(text), slow(seconds), crash(), count() -> how many echo calls ran,
big(n) -> n characters of output.
"""

import json
import os
import sys
import time

echo_calls = 0
sys.stderr.write("fake server started\n")
sys.stderr.flush()


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def text(rpc_id, value, error=False):
    send({"jsonrpc": "2.0", "id": rpc_id,
          "result": {"content": [{"type": "text", "text": value}], "isError": error}})


def handle(msg):
    global echo_calls
    method, rpc_id = msg.get("method"), msg.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": rpc_id, "result": {
            "protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "1"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": rpc_id, "result": {"tools": [
            {"name": n, "inputSchema": {"type": "object"}} for n in ("echo", "slow", "crash", "count", "big")]}})
    elif method == "tools/call":
        name = msg["params"]["name"]
        args = msg["params"].get("arguments", {})
        if name == "echo":
            echo_calls += 1
            text(rpc_id, "echo:" + str(args.get("text", "")))
        elif name == "slow":
            time.sleep(float(args.get("seconds", 1)))
            text(rpc_id, "done")
        elif name == "crash":
            os._exit(3)
        elif name == "count":
            text(rpc_id, str(echo_calls))
        elif name == "big":
            text(rpc_id, "x" * int(args.get("n", 1000)))
        else:
            send({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32602, "message": "unknown tool"}})
    elif method == "ping":
        send({"jsonrpc": "2.0", "id": rpc_id, "result": {}})
    elif rpc_id is not None:
        send({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": "method not found"}})


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        data = json.loads(line)
    except ValueError:
        continue
    for m in (data if isinstance(data, list) else [data]):
        handle(m)
