"""Expose a FastMCP server on Streamable HTTP (/mcp) and legacy SSE (/sse).

Usage: uvicorn asgi_both:app --host 0.0.0.0 --port <port>
The target module (env MCP_MODULE) must define a module-level ``mcp`` FastMCP.

Routing is a plain ASGI dispatcher instead of Starlette routes on purpose:

* ``Route(endpoint=...)`` wraps the handler and re-sends a Response after the
  streaming response is already finished, which makes uvicorn raise
  "Unexpected ASGI message 'http.response.start' sent, after response
  already completed" on every disconnected client.
* ``Mount("/mcp")`` only matches ``/mcp/...`` and turns a bare ``/mcp``
  request into a 307 redirect that many MCP clients do not follow.
"""

import importlib
import os
from contextlib import asynccontextmanager

from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette

server = importlib.import_module(os.environ["MCP_MODULE"].replace("-", "_"))
mcp = server.mcp
lowlevel = mcp._mcp_server
security = mcp.settings.transport_security

sse = SseServerTransport("/messages/", security_settings=security)
streamable = StreamableHTTPSessionManager(app=lowlevel, security_settings=security)


@asynccontextmanager
async def _lifespan(_app):
    async with streamable.run():
        yield


# Starlette is reused only for its lifespan handling and the default 404.
fallback = Starlette(routes=[], lifespan=_lifespan)


async def handle_sse(scope, receive, send):
    async with sse.connect_sse(scope, receive, send) as streams:
        await lowlevel.run(
            streams[0],
            streams[1],
            lowlevel.create_initialization_options(),
        )


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        await fallback(scope, receive, send)
        return

    path = scope.get("path", "")
    root_path = scope.get("root_path", "")
    if root_path and path.startswith(root_path):
        path = path[len(root_path):]
    if len(path) > 1:
        path = path.rstrip("/")

    if path == "/sse":
        await handle_sse(scope, receive, send)
    elif path == "/mcp":
        await streamable.handle_request(scope, receive, send)
    elif path == "/messages" or path.startswith("/messages/"):
        await sse.handle_post_message(scope, receive, send)
    else:
        await fallback(scope, receive, send)
