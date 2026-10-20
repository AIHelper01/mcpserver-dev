# Adapted from https://github.com/modelcontextprotocol/python-sdk
"""
FastMCP quickstart example.

Run from this directory:
    python w-mcp_demo_server.py
"""

import os
from typing import Literal

from mcp.server.fastmcp import FastMCP


# Create an MCP server
def _port():
    try:
        port = int(os.getenv("DEMO_MCP_PORT", "8000"))
        return port if 1 <= port <= 65535 else 8000
    except ValueError:
        return 8000


mcp = FastMCP(
    "w-MCPDemo",
    instructions=(
        "w-MCPDemo：教学演示用 MCP 服务，基于 Streamable HTTP。"
        "工具只用于演示 MCP 调用流程，不访问任何外部服务。"
        "\n"
        "w-MCPDemo: teaching demo MCP server over Streamable HTTP. Tools exist to "
        "demonstrate MCP call flows and touch no external services."
    ),
    json_response=True,
    host=os.getenv("DEMO_MCP_HOST", "0.0.0.0").strip() or "0.0.0.0",
    port=_port(),
)


def _name(name: str, language: str = "en") -> str:
    if not name.strip() or len(name) > 200:
        raise ValueError(
            "姓名不能为空，且最多 200 个字符。"
            if language == "zh"
            else "Name must contain 1..200 characters."
        )
    return name


@mcp.tool()
def ping() -> dict:
    """
    探活：检查演示服务是否在线，不执行工具或提示词。

    Check the demo server without executing a tool or prompt.
    """
    return {
        "ok": True,
        "server": "w-MCPDemo",
        "transport": "streamable-http",
        "port": mcp.settings.port,
    }


# Add an addition tool
@mcp.tool()
def add(a: int, b: int) -> int:
    """
    计算两个数字之和。

    Add two numbers
    """
    return a + b


# Add a dynamic greeting resource
@mcp.resource("greeting://{name}")
def get_greeting(name: str) -> str:
    """Get an English greeting, preserving the supplied name."""
    return get_localized_greeting(name, "en")


@mcp.resource("greeting://{name}/{language}")
def get_localized_greeting(name: str, language: Literal["en", "zh"]) -> str:
    """Get an English or Chinese greeting using an explicit language in the URI."""
    name = _name(name, language)
    return f"你好，{name}！" if language == "zh" else f"Hello, {name}!"


# Add a prompt
@mcp.prompt()
def greet_user(
    name: str, style: str = "friendly", language: Literal["en", "zh"] = "en"
) -> str:
    """Generate a greeting prompt in English (default) or Chinese (language=zh)."""
    name = _name(name, language)
    english_styles = {
        "friendly": "Please write a warm, friendly greeting",
        "formal": "Please write a formal, professional greeting",
        "casual": "Please write a casual, relaxed greeting",
    }

    chinese_styles = {
        "friendly": "请写一段温暖友好的问候语",
        "formal": "请写一段正式专业的问候语",
        "casual": "请写一段轻松随意的问候语",
    }
    if language == "zh":
        return f"{chinese_styles.get(style, chinese_styles['friendly'])}，对方的名字是{name}。"
    return f"{english_styles.get(style, english_styles['friendly'])} for someone named {name}."


# Run with streamable HTTP transport
async def _run_http():
    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(
            mcp.streamable_http_app(),
            host=mcp.settings.host,
            port=mcp.settings.port,
            log_level=mcp.settings.log_level.lower(),
            timeout_keep_alive=1,
            timeout_graceful_shutdown=2,
        )
    )
    await server.serve()


def run_server():
    import anyio

    try:
        anyio.run(_run_http)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run_server()
