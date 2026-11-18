"""HTTP gateway: puts the same Allow/Block gate in front of a *remote* MCP endpoint.

Claude Web (claude.ai) calls MCP servers from Anthropic's cloud, so the only
place we can intercept is the endpoint itself. The gateway speaks MCP
"Streamable HTTP" to Claude and forwards to the real server (a local stdio
server it launches, or an upstream HTTP MCP URL).
"""

from .app import Gateway, create_app

__all__ = ["Gateway", "create_app"]
