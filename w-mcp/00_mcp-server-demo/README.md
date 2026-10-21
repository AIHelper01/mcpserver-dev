# w-MCP Demo 与 w-Time MCP

[English](../../../english/02_mcp/cases/00_mcp-server-demo/README.md) | 简体中文

保留两种传输的教学区分：`w-mcp_demo_server.py` 通过 HTTP 展示 tool、resource、prompt；
`w-time_mcp_server.py` 通过 stdio 提供带时区的时间工具。仅保留这两个入口，不保留旧名脚本。

## 安装与启动

依赖已声明在本目录的 `pyproject.toml` / `uv.lock`（`mcp>=1.12,<2`、`uvicorn`，
以及仅 Windows 生效的 `tzdata`，要求 Python ≥3.11）。`uv run` 会自动创建 `.venv`
并按需拉取 Python 3.12，macOS / Linux / Windows 用的是同一条命令。

```bash
# stdio 时间服务（由 MCP 客户端拉起，或手动调试）
uv run python w-time_mcp_server.py

# HTTP 演示服务，仅 /mcp
uv run python w-mcp_demo_server.py

# 同一个演示服务同时提供 /mcp 与 /sse
uv run python asgi_both.py --file w-mcp_demo_server.py --port 8000
```

- `tzdata` 用 `sys_platform == 'win32'` 标记：Windows 缺少系统 IANA 数据库时自动安装，
  macOS / Linux 不装（用系统时区数据）。UTC 和服务端本地时间不依赖它。
- 没有 uv 时退回 pip：`python -m pip install "mcp>=1.12,<2" uvicorn tzdata`
  后直接 `python w-mcp_demo_server.py`（需 Python ≥3.11）。
- Windows PowerShell 换端口写 `$env:DEMO_MCP_PORT = "8000"`，不是 `VAR=value` 前缀；
  或者直接用垫片的 `--port` 参数。

### Docker（免装 Python）

```bash
cd ../..            # 仓库根 mcpserver-dev
docker compose up -d --build mcp-demo
docker compose logs -f mcp-demo
```

容器名 `mcp-demo`，端口 16684，同时提供 `/mcp` 与 `/sse`；镜像内
`DEMO_MCP_PORT=16684`，默认不校验 Host，局域网可直接连。
stdio 的 `w-time_mcp_server.py` 不进容器，由 MCP 客户端在需要的那台机器上拉起。

客户端连 `http://127.0.0.1:16684/mcp`（本机直跑时仍是 8000）。

## HTTP 基础示例

```bash
python w-mcp_demo_server.py
```

服务标识 `w-MCPDemo`，默认地址 `http://127.0.0.1:8000/mcp`。
`DEMO_MCP_HOST` 默认 0.0.0.0，仅本机可设 127.0.0.1；`DEMO_MCP_PORT` 默认 8000，无效值回退默认。
keep-alive 和优雅关闭等待分别为 1、2 秒。Host/Origin 校验沿用 FastMCP 默认，不新增鉴权。

| 能力 | 行为 |
| --- | --- |
| `ping()` | 返回服务名、传输和端口 |
| `add(a,b)` | 整数相加，返回不区分语言的数字 |
| `greeting://Alice` | 英文问候 resource |
| `greeting://Alice/en`、`greeting://Alice/zh` | 按 URI 选择语言 |
| `greet_user(name, style="friendly", language="en")` | 生成问候指令，支持 friendly/formal/casual，未知风格回退 friendly |

姓名保留原文，必须有非空白字符且不超过 200 字符。校验错误由 MCP 返回。
语言放入 resource URI，以匹配模板参数；prompt 返回写作指令，不是模型生成的最终问候。

## stdio 时间工具

```bash
python w-time_mcp_server.py
```

服务标识 `w-TimeMCP`，由 MCP 客户端启动并管理标准流；不要向 stdout 添加普通日志。

| 工具 | 行为 |
| --- | --- |
| `get_current_time(timezone_name="", language="en")` | 默认服务器本地时间；可选择 UTC 或 IANA 时区；返回带偏移 ISO-8601、Unix 时间戳、UTC 偏移及兼容的 current_time 字段 |
| `convert_time(iso_time, to_timezone="UTC", from_timezone="", language="en")` | 转换 ISO-8601 时间；无偏移输入必须提供源时区 |

显式 UTC 偏移不能与 from_timezone 同时使用。夏令时造成的歧义时刻或不存在时刻会拒绝，需用显式偏移选择具体瞬间。
无效时区返回本地化错误；也可能需要安装时区数据。服务器时钟不一定与客户端本地时间相同。

## 在 Agent 中配置

两个服务分工不同：`w-MCPDemo` 是常驻 HTTP 服务，先启动再连接；`w-TimeMCP` 是 stdio 服务，
由 Agent 自己拉起并管理进程，不需要预先启动。

### Codex

Streamable HTTP 服务一行添加，`codex mcp list` 校验：

```bash
codex mcp add w-MCPDemo --url http://127.0.0.1:8000/mcp
codex mcp list
```

也可以直接写 `~/.codex/config.toml`。Codex 用 `url` 表示 Streamable HTTP，
用 `command` + `args` 表示 stdio（`--project` 让 uv 用本目录的依赖锁）：

```toml
[mcp_servers.w-MCPDemo]
url = "http://127.0.0.1:8000/mcp"

[mcp_servers.w-TimeMCP]
command = "uv"
args = [
  "run", "--project", "/绝对路径/w-mcp/00_mcp-server-demo",
  "python", "/绝对路径/w-mcp/00_mcp-server-demo/w-time_mcp_server.py",
]
startup_timeout_sec = 20
```

改完配置需重启 Codex 才会重新加载工具列表。只想暴露部分工具时，可加
`enabled_tools = ["ping", "add"]`。

### Cherry Studio

界面路径：设置 → MCP 服务 → 添加服务器。

- `w-MCPDemo`：类型选 **Streamable HTTP**，服务器地址填 `http://127.0.0.1:8000/mcp`，保存后打开开关。
- `w-TimeMCP`：类型选 **STDIO**，命令填 `uv`，参数填上面 `args` 数组的内容（逐行填写），
  其中路径必须是绝对路径。`--project` 指向本目录，依赖由 `pyproject.toml` / `uv.lock` 决定，客户端配置里不必再写版本号。

Cherry Studio 的远程地址字段是 `baseUrl`（旧版本兼容 `url`），JSON 形式：

```json
{
  "mcpServers": {
    "w-MCPDemo": {
      "name": "w-MCPDemo",
      "description": "MCP 教学演示服务：tool / resource / prompt",
      "type": "streamableHttp",
      "baseUrl": "http://127.0.0.1:8000/mcp",
      "isActive": true
    },
    "w-TimeMCP": {
      "name": "w-TimeMCP",
      "description": "带时区的时间工具",
      "type": "stdio",
      "command": "uv",
      "args": [
        "run", "--project", "/绝对路径/w-mcp/00_mcp-server-demo",
        "python", "/绝对路径/w-mcp/00_mcp-server-demo/w-time_mcp_server.py"
      ],
      "isActive": false
    }
  }
}
```

配置完成后在 Agent 里勾选该 MCP 服务，工具才会进入对话。
本示例面向可信教学环境，不含生产鉴权策略；升级入口名称后重启服务并刷新客户端工具列表。

## 验证

父级 `mcp` 目录的联合测试还需安装 python-dotenv、requests：

```bash
python -B -m unittest -v test_weather_demo test_bilingual_tools
```

覆盖中英文、真实 HTTP tools/resources/prompts、stdio 时间调用、UTC/IANA 转换及夏令时边界。
天气使用本地模拟数据。见 [Showcase](SHOWCASE.md)。
