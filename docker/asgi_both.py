"""Expose a FastMCP server on Streamable HTTP (/mcp) and legacy SSE (/sse).

Usage: uvicorn asgi_both:app --host 0.0.0.0 --port <port>
The target module (env MCP_MODULE) must define a module-level ``mcp`` FastMCP.
"""

import importlib
import os
from contextlib import asynccontextmanager

from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route

server = importlib.import_module(os.environ["MCP_MODULE"])
mcp = server.mcp
lowlevel = mcp._mcp_server
security = mcp.settings.transport_security

sse = SseServerTransport("/messages/", security_settings=security)
streamable = StreamableHTTPSessionManager(app=lowlevel, security_settings=security)


async def handle_sse(request: Request) -> Response:
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await lowlevel.run(
            streams[0],
            streams[1],
            lowlevel.create_initialization_options(),
        )
    return Response()


async def handle_streamable(request: Request) -> Response:
    await streamable.handle_request(request.scope, request.receive, request._send)
    return Response()


@asynccontextmanager
async def _lifespan(app):
    async with streamable.run():
        yield


app = Starlette(
    routes=[
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
        Mount("/messages/", app=sse.handle_post_message),
        Route(
            "/mcp",
            endpoint=handle_streamable,
            methods=["GET", "POST", "DELETE", "PUT", "PATCH", "OPTIONS", "HEAD"],
        ),
    ],
    lifespan=_lifespan,
)
