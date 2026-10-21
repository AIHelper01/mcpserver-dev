"""Run a FastMCP server on Streamable HTTP (/mcp) and legacy SSE (/sse).

Two ways to start (identical on macOS / Linux / Windows):

1. Local dev with uv, from the service directory -- no environment variables:

       uv run python asgi_both.py --file w-openweather_mcp_server.py

   Optional: --host 0.0.0.0 --port 16681 --log-level info

2. Container (uvicorn import string), env MCP_MODULE or MCP_SERVER_FILE required:

       uvicorn asgi_both:app --host 0.0.0.0 --port 16681

The target module must define a module-level ``mcp`` FastMCP instance.

Routing is a plain ASGI dispatcher instead of Starlette routes on purpose:

* ``Route(endpoint=...)`` wraps the handler and re-sends a Response after the
  streaming response is already finished, which makes uvicorn raise
  "Unexpected ASGI message 'http.response.start' sent, after response
  already completed" on every disconnected client.
* ``Mount("/mcp")`` only matches ``/mcp/...`` and turns a bare ``/mcp``
  request into a 307 redirect that many MCP clients do not follow.

``GET /health`` answers 200 JSON for container health checks; unknown paths 404.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette


def load_server_module(target: str):
    """Import the server by file path (hyphens/unicode allowed) or module name."""
    candidate = Path(target).expanduser()
    if candidate.suffix == ".py" or candidate.exists():
        path = candidate.resolve()
        if not path.is_file():
            raise SystemExit(f"server file not found: {path}")
        name = path.stem.replace("-", "_")
        sys.path.insert(0, str(path.parent))
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    return importlib.import_module(target.replace("-", "_"))


def build_app(target: str, host: str | None = None, port: int | None = None):
    """Build the dual-transport ASGI app for one FastMCP server."""
    module = load_server_module(target)
    mcp = getattr(module, "mcp", None)
    if mcp is None:
        raise SystemExit(f"{target} does not define a module-level 'mcp' FastMCP")

    if host is not None:
        mcp.settings.host = host
    if port is not None:
        mcp.settings.port = port
    # Keep Host/DNS-rebinding checks consistent with the port actually served.
    security = mcp.settings.transport_security
    if security is not None and port is not None:
        for pattern in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"):
            if pattern not in security.allowed_hosts:
                security.allowed_hosts.append(pattern)

    lowlevel = mcp._mcp_server
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

        if path == "/health":
            payload = json.dumps(
                {"ok": True, "server": app.mcp_name, "transports": ["/mcp", "/sse"]}
            ).encode()
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode()),
                ],
            })
            await send({"type": "http.response.body", "body": payload})
            return

        if path == "/sse":
            await handle_sse(scope, receive, send)
        elif path == "/mcp":
            await streamable.handle_request(scope, receive, send)
        elif path == "/messages" or path.startswith("/messages/"):
            await sse.handle_post_message(scope, receive, send)
        else:
            await fallback(scope, receive, send)

    app.mcp_host = mcp.settings.host
    app.mcp_port = mcp.settings.port
    app.mcp_name = mcp.name
    return app


_target = os.environ.get("MCP_SERVER_FILE") or os.environ.get("MCP_MODULE")
app = build_app(_target) if _target else None


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Serve one FastMCP server on /mcp (Streamable HTTP) and /sse (SSE).",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--file", help="path to the server script, e.g. w-xxx_mcp_server.py")
    group.add_argument("--module", help="importable module name, e.g. mcp_server")
    parser.add_argument("--host", default=None, help="default: the server's own host setting")
    parser.add_argument("--port", type=int, default=None, help="default: the server's own port setting")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    import uvicorn

    target = args.file or args.module or _target
    if not target:
        parser.error("provide --file <script.py> or --module <name>")

    handler = build_app(target, host=args.host, port=args.port)
    host, port = handler.mcp_host, handler.mcp_port
    print(f"[asgi_both] {handler.mcp_name}: http://{host}:{port}/mcp  and  /sse", flush=True)
    uvicorn.run(handler, host=host, port=port, log_level=args.log_level)


if __name__ == "__main__":
    main()
