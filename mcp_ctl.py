#!/usr/bin/env python3
"""One-command control for the MCP containers: start, stop, status, verify.

Cross-platform (macOS / Linux / Windows). Requires only Python 3.10+ and Docker;
uses the standard library, so it runs from a bare interpreter or via uv:

    python mcp_ctl.py up        # 构建并启动全部（= docker compose up -d --build）
    python mcp_ctl.py status    # 容器状态 + healthcheck 结果
    python mcp_ctl.py check     # 端到端体检：/health、/mcp、工具调用、/sse
    python mcp_ctl.py doctor    # 前置检查：docker、.env、端口占用
    python mcp_ctl.py down      # 停止并删除容器
    python mcp_ctl.py logs mcp-weather

`make <target>` 是同一脚本的快捷入口（Windows 无 make 时直接用本脚本）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"

# (compose 服务名, 容器名, 端口, 真实工具调用样例, 是否有 /health 端点)
# /health 由 docker/asgi_both.py 提供；third-party 12306 没有，只要端口有 HTTP 响应即算存活。
SERVICES = [
    ("mcp-weather", "mcp-weather", 16681, ("get_weather", {"location": "Suzhou"}), True),
    ("mcp-file", "mcp-file", 16682, ("list_files", {"directory": "files"}), True),
    ("mcp-12306", "mcp-12306", 16683, ("get-current-date", {}), False),
    ("mcp-demo", "mcp-demo", 16684, ("add", {"a": 2, "b": 40}), True),
]

PASSTHROUGH = ("up", "down", "restart", "build", "ps", "logs")


def compose(*args: str, capture: bool = False) -> subprocess.CompletedProcess:
    cmd = [docker_cmd(), "compose"] + list(args)
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=capture)


def docker_cmd() -> str:
    docker = shutil.which("docker")
    if not docker:
        sys.exit("未找到 docker，请先安装 Docker Desktop / docker engine")
    return docker


def read_env_key(key: str) -> str | None:
    if os.environ.get(key):
        return os.environ[key]
    if not ENV_FILE.is_file():
        return None
    # 兼容 KEY=value / KEY = value 与 CRLF；compose 与 python-dotenv 也都能这样解析
    for line in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() != key:
            continue
        return value.strip().strip('"').strip("'") or None
    return None


def port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def container_state(name: str) -> tuple[str, str, int]:
    """Return (state, health, restart_count) for one container; "-" when absent."""
    out = subprocess.run(
        [docker_cmd(), "inspect", "-f",
         "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}",
         name],
        text=True, capture_output=True,
    )
    if out.returncode != 0:
        return "-", "-", 0
    state, health, restarts = out.stdout.strip().split("|")
    return state, health, int(restarts or 0)


def rpc(port: int, payload: dict, session_id: str | None = None):
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/mcp",
        data=json.dumps(payload).encode(), headers=headers, method="POST",
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.headers.get("Mcp-Session-Id"), resp.read().decode()


def parse_sse(body: str) -> dict | None:
    for line in body.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    return json.loads(body) if body.strip() else None


def check_service(container: str, port: int, tool, has_health: bool) -> bool:
    """Verify one service end to end and print one line per service."""
    state, health, restarts = container_state(container)
    if state != "running":
        print(f"  {container:<12} :{port}  [FAIL] state={state} -> 未运行")
        return False

    problems: list[str] = []
    checks: dict[str, bool] = {}

    # 1) 存活：/health 必须 200；无 /health 的第三方服务只要求端口有 HTTP 响应
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as resp:
            checks["health端点"] = resp.status == 200
    except urllib.error.HTTPError as exc:
        # 第三方服务没有 /health，能返回任意 <500 的 HTTP 响应就说明进程活着
        checks["health端点"] = (not has_health) and exc.code < 500
    except Exception as exc:  # noqa: BLE001 - 体检要把任何异常如实报告
        checks["health端点"] = False
        problems.append(f"/health: {type(exc).__name__}")
    if not has_health:
        checks.pop("health端点", None)

    # 2) MCP 会话：initialize -> tools/list -> tools/call
    name, tools, info = "-", 0, ""
    try:
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                           "clientInfo": {"name": "mcp_ctl", "version": "1"}}}
        sid, body = rpc(port, init)
        checks["/mcp initialize"] = True
        name = (parse_sse(body) or {}).get("result", {}).get("serverInfo", {}).get("name", "-")
        rpc(port, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
        _, body = rpc(port, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, sid)
        tools = len(parse_sse(body)["result"]["tools"])
        checks["tools/list"] = tools > 0
        _, body = rpc(port, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                             "params": {"name": tool[0], "arguments": tool[1]}}, sid)
        result = parse_sse(body)["result"]
        checks["tools/call"] = not result.get("isError")
        text = " ".join(c.get("text", "") for c in result.get("content", []))
        info = f"{tool[0]} -> {' '.join(text.split())[:60]}"
    except Exception as exc:  # noqa: BLE001
        problems.append(f"/mcp: {type(exc).__name__}: {exc}")

    # 3) SSE：首帧必须是 endpoint 事件；连接被超时打断同样说明流已建立
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/sse", timeout=3) as resp:
            checks["/sse"] = b"event: endpoint" in resp.readline()
    except urllib.error.HTTPError as exc:
        problems.append(f"/sse: HTTP {exc.code}")
        checks["/sse"] = False
    except Exception:  # noqa: BLE001
        checks["/sse"] = True

    if health not in ("healthy", "none"):
        problems.append(f"容器 healthcheck={health}")
    failed = [label for label, ok in checks.items() if not ok]
    ok = not failed and not problems and state == "running"
    print(f"  {container:<12} :{port}  [{'OK  ' if ok else 'FAIL'}] {name:<18} "
          f"tools={tools:<3} health={health:<9} restarts={restarts}")
    if info:
        print(f"                 {info}")
    for item in failed:
        print(f"                 ! 检查未通过：{item}")
    for problem in problems:
        print(f"                 ! {problem}")
    return ok


def cmd_status() -> int:
    for _, container, port, _, _ in SERVICES:
        state, health, restarts = container_state(container)
        print(f"  {container:<12} :{port}  state={state:<9} health={health:<9} restarts={restarts}")
    return 0


def cmd_doctor() -> int:
    ok = True
    docker = shutil.which("docker")
    print(f"  docker      : {docker or '缺失 -> 安装 Docker Desktop / engine'}")
    ok &= bool(docker)
    probe = subprocess.run([docker, "compose", "version"], text=True, capture_output=True) if docker else None
    compose_ok = probe is not None and probe.returncode == 0
    print(f"  compose     : {(probe.stdout or '').splitlines()[0] if compose_ok else '缺失 -> 需要 compose v2 插件'}")
    ok &= compose_ok
    key = read_env_key("OPENWEATHER_API_KEY")
    print(f"  .env        : {'OPENWEATHER_API_KEY 已配置' if key else '缺 OPENWEATHER_API_KEY -> mcp-weather 无法取真实天气'}")
    ok &= bool(key)
    for _, _, port, _, _ in SERVICES:
        print(f"  端口 {port}   : {'已监听' if port_listening(port) else '空闲（服务未起）'}")
    print("  结论        :", "OK" if ok else "有前置项未满足，见上面提示")
    return 0 if ok else 1


def cmd_check() -> int:
    print("端到端体检（/health · /mcp · 工具调用 · /sse）：")
    all_ok = True
    for _, container, port, tool, has_health in SERVICES:
        all_ok &= check_service(container, port, tool, has_health)
    print("全部通过" if all_ok else "存在失败项，先看 docker logs，再 make up 重建")
    return 0 if all_ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", default="status",
                        choices=PASSTHROUGH + ("status", "check", "doctor"))
    parser.add_argument("args", nargs="*", help="传给 docker compose 的额外参数（服务名、日志服务等）")
    parser.add_argument("--no-build", action="store_true", help="up 时跳过构建")
    ns = parser.parse_args()

    if ns.command == "status":
        return cmd_status()
    if ns.command == "check":
        return cmd_check()
    if ns.command == "doctor":
        return cmd_doctor()
    if ns.command == "up" and not ns.no_build:
        return compose("up", "-d", "--build", *ns.args).returncode
    if ns.command == "logs" and ns.args:
        return compose("logs", "-f", "--tail", "100", *ns.args).returncode
    return compose(ns.command, *ns.args).returncode


if __name__ == "__main__":
    sys.exit(main())
