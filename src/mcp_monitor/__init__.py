"""MCP Communication Monitor.

Sits between Claude (Desktop / Code / Web) and MCP servers, holds every
tool call until the user clicks Allow or Block in a popup.

Modules
-------
protocol   JSON-RPC helpers: classify messages, build denials, redact secrets.
config     Settings and the per-user data directory.
ipc        Shim <-> daemon channel (localhost, token-authenticated).
shim       Stdio proxy placed in front of one MCP server.
daemon     Background app: pending-request broker, popup UI, audit log.
installer  Rewrites Claude config files to route servers through the shim.
"""

__version__ = "0.1.0"
