# w-MCP Demo 与 w-Time MCP

[English](../../../english/02_mcp/cases/00_mcp-server-demo/README.md) | 简体中文

保留两种传输的教学区分：`w-mcp_demo_server.py` 通过 HTTP 展示 tool、resource、prompt；
`w-time_mcp_server.py` 通过 stdio 提供带时区的时间工具。仅保留这两个入口，不保留旧名脚本。

## 安装

依赖 Python 3.12 与 `mcp>=1.12,<2`；`w-time_mcp_server.py` 另需 `tzdata`。

### 方式一：pip 装进现有环境

```bash
python -m pip install "mcp>=1.12,<2" tzdata
```

`tzdata` 为没有系统时区数据库的环境（常见于 Windows）提供 IANA 数据。UTC 和服务器本地时间不依赖它。

### 方式二：uv 临时环境免安装启动（课堂推荐）

不污染当前 Python，一条命令起服务：

```bash
# HTTP 演示服务（tool / resource / prompt）
uv run --no-project --with "mcp>=1.12,<2" python w-mcp_demo_server.py

# stdio 时间服务
uv run --no-project --with "mcp>=1.12,<2" --with tzdata python w-time_mcp_server.py
```

### 方式三：uv 固定虚拟环境

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python "mcp>=1.12,<2" tzdata
uv run --python .venv/bin/python w-mcp_demo_server.py
```

要复用本仓库配套内核环境，把 `.venv/bin/python` 换成该环境的绝对路径即可，
例如 `uv pip install --python <你的路径>/env_agent_uv/.venv/bin/python "mcp>=1.12,<2" tzdata`。

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
用 `command` + `args` 表示 stdio：

```toml
[mcp_servers.w-MCPDemo]
url = "http://127.0.0.1:8000/mcp"

[mcp_servers.w-TimeMCP]
command = "uv"
args = [
  "run", "--no-project",
  "--with", "mcp>=1.12,<2", "--with", "tzdata",
  "python", "/绝对路径/w-time_mcp_server.py",
]
startup_timeout_sec = 20
```

改完配置需重启 Codex 才会重新加载工具列表。只想暴露部分工具时，可加
`enabled_tools = ["ping", "add"]`。

### Cherry Studio

界面路径：设置 → MCP 服务 → 添加服务器。

- `w-MCPDemo`：类型选 **Streamable HTTP**，服务器地址填 `http://127.0.0.1:8000/mcp`，保存后打开开关。
- `w-TimeMCP`：类型选 **STDIO**，命令填 `uv`，参数填上面 `args` 数组的内容（逐行填写），
  其中脚本路径必须是绝对路径。

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
        "run", "--no-project",
        "--with", "mcp>=1.12,<2", "--with", "tzdata",
        "python", "/绝对路径/w-time_mcp_server.py"
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
