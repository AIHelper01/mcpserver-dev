# mcpserver-dev — MCP Server 开发基地

本目录用于开发、容器化和运行 MCP Server。
自研 server 放 `w-mcp/`，下载的第三方 server 源码放 `third-party/`。
新增自研 server 请照本指南的既有模式落地。

## 现有服务

目录分两类：`w-mcp/` = 自研；`third-party/` = 公网下载的第三方源码（非本仓库开发，只做容器化运行）。

| 目录 | 服务 | 容器 | 端口 | 端点 |
|---|---|---|---|---|
| w-mcp/00_mcp-server-demo | 教学 demo（ping/时间） | 未容器化 | 8000 | uv 直跑；`w-time_mcp_server.py` 为 stdio |
| w-mcp/01_weather-mcp-server | w-OpenWeatherMCP | mcp-weather | 16681 | `/mcp` + `/sse` |
| w-mcp/02_file-mcp-server | w-FileWorkbenchMCP | mcp-file | 16682 | `/mcp` + `/sse` |
| third-party/12306-mcp | 12306-mcp v0.3.10（第三方） | mcp-12306 | 16683 | `/mcp` + `/sse` |

- 12306 运行用镜像 `12306-mcp:latest`（源自上游 Dockerfile：`npm install 12306-mcp`），
  compose 覆盖 command 把端口改为 16683；`third-party/12306-mcp` 为上游原始源码
  （含 .git 与 node_modules），仅作参考，不参与构建。

三个容器均由 `docker-compose.yml` 统一管理，全部同时暴露
Streamable HTTP（`/mcp`）与 SSE（`/sse`）两种传输。

## 一、如何开发一个 MCP Server

技术选型：Python 3.12 + 官方 `mcp` SDK 的 FastMCP（`mcp>=1.12,<2`），
按需追加 `requests` / `python-dotenv`。

骨架（关键三段）：

```python
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
import os

mcp = FastMCP(
    "w-MyMCP",                      # 服务名
    instructions="使用指引...",       # 告诉大模型这个服务能干什么、怎么用
    host=os.getenv("MY_MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MY_MCP_PORT", "16684")),   # 端口走环境变量，别写死
    transport_security=TransportSecuritySettings(   # DNS rebinding / Host 校验
        allowed_hosts=[f"127.0.0.1:{os.getenv('MY_MCP_PORT', '16684')}",
                       f"localhost:{os.getenv('MY_MCP_PORT', '16684')}"],
    ),
)

@mcp.tool()
def my_tool(location: str, language: str = "en") -> dict:
    """工具描述写进 docstring，参数用类型注解 + 默认值，大模型据此生成调用。"""
    return {"ok": True, ...}

if __name__ == "__main__":
    mcp.run(transport="streamable-http")    # 开发期最简方式
```

开发要点：
- 端口、密钥、允许主机全部走环境变量；密钥放根目录 `.env`（已 gitignore）。
- 返回结构化 dict：成功 `{"ok": true, ...}`，失败 `{"ok": false, "error": ...}`，
  不要向调用方抛裸异常。
- 中文场景提供 `language="zh"` 参数做双语输出；地点名等输入原样透传上游 API。
- 文件类工具注意路径解析（空 base_dir 以进程 cwd 为根）与备份策略（参考 02 的
  `.deleted_files/`、`.snapshots/`）。
- 分配新端口顺延：16684、16685…

本地调试：

```bash
uv run python w-xxx_mcp_server.py                # 直跑（仅 /mcp，依赖由 pyproject 自动装）
uv run python asgi_both.py --file w-xxx_mcp_server.py   # 双传输：/mcp + /sse
npx @modelcontextprotocol/inspector              # 官方调试器
curl -X POST http://127.0.0.1:<port>/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}'
```

## 二、启动方式与跨平台支持

四条启动路径，命令在 macOS / Linux / Windows 上**逐字相同**，只有临时设环境变量的
语法不同：

| 方式 | 命令（在服务目录内执行） | 传输 | 适用场景 |
|---|---|---|---|
| 脚本直跑 | `uv run python w-xxx_mcp_server.py` | 仅 `/mcp` | 开发调试（FastMCP 原生） |
| 双传输垫片 | `uv run python asgi_both.py --file w-xxx_mcp_server.py` | `/mcp` + `/sse` | 兼容只支持 SSE 的客户端 |
| stdio | `uv run python w-time_mcp_server.py` | stdio | 客户端本地拉起进程，不占端口 |
| Docker | `docker compose up -d --build`（仓库根） | `/mcp` + `/sse` | 常驻、跨机器、免装 Python |

已实测矩阵（本机 macOS + uv 0.11 + Docker，2026-10-21）：

| 服务 | uv 直跑 `/mcp` | uv 垫片 `/mcp`+`/sse` | uv stdio | Docker `/mcp`+`/sse` |
|---|---|---|---|---|
| 00 demo | ✅ :17786 | ✅ :17783 | ✅ w-TimeMCP | 未容器化 |
| 01 weather | ✅ :17784 | ✅ :17781 | 不适用 | ✅ :16681 |
| 02 file | ✅ :17785 | ✅ :17782 | 不适用 | ✅ :16682 |
| third-party 12306 | 需 Node，不由本仓库维护 | 同左 | — | ✅ :16683 |

### 前置要求

- **uv**（`uv --version`，建议 ≥0.5）：安装见 <https://docs.astral.sh/uv/>。
  每个自研服务目录都有 `pyproject.toml` + `.python-version`，`uv run` 会自建
  `.venv` 并按需拉取 Python 3.12，因此**不必预装 Python**，三系统同一条命令。
- **依赖锁定**：`uv.lock` 需提交（已包含 `sys_platform == 'win32'` 解析标记，
  Windows 上解析结果与 macOS/Linux 一致）；`.venv/` 已在 `.gitignore`。
- **换行符**：`.gitattributes` 强制 `*.py` 等为 LF，避免 Windows 检出 CRLF
  导致容器内启动异常。
- **Docker**：macOS/Windows 用 Docker Desktop；Linux 用 docker + compose 插件。
- **Python 下限**：`requires-python = ">=3.11"`（代码用了 `datetime.UTC`）。
  用系统 Python 直跑时别低于 3.11，容器内是 3.12。

### 命令示例

macOS / Linux（bash、zsh）：

```bash
cd w-mcp/01_weather-mcp-server
uv run python asgi_both.py --file w-openweather_mcp_server.py --port 16681

# 只有走脚本原生入口时才需要环境变量
WEATHER_MCP_PORT=16681 uv run python w-openweather_mcp_server.py
```

Windows（PowerShell）：

```powershell
cd w-mcp\01_weather-mcp-server
uv run python asgi_both.py --file w-openweather_mcp_server.py --port 16681

# PowerShell 不支持 VAR=value 前缀写法，改用 $env:
$env:WEATHER_MCP_PORT = "16681"
uv run python w-openweather_mcp_server.py
Remove-Item Env:WEATHER_MCP_PORT
```

> 垫片提供 `--file/--module/--host/--port/--log-level` 参数，就是为了让启动命令
> **不依赖 shell 的环境变量语法**，跨平台优先用它。省略 `--port` 时沿用服务自己的
> 默认端口；显式指定端口会自动把 `127.0.0.1:<port>`、`localhost:<port>` 追加进
> Host 白名单。局域网访问仍需各服务的 `*_ALLOWED_HOSTS`。

>
> stdio 类型的客户端配置（Codex `command`/`args`、Cherry Studio STDIO）统一写成
> `uv run --project <服务目录> python <脚本绝对路径>`：依赖由该目录的 `uv.lock`
> 决定，配置里不出现版本号，三系统只是路径写法不同（已实测）。

### 系统差异清单

| 项目 | macOS | Linux | Windows |
|---|---|---|---|
| `uv run`（stdio / `/mcp` / 双传输） | ✅ 已实测 | 同一套 uv + Python，行为一致 | 同一套 uv + Python，行为一致 |
| `docker compose` | ✅ 已实测 | ✅ 需 docker + compose 插件 | ✅ 需 Docker Desktop（WSL2 后端） |
| 根目录 `.env` 自动加载 | ✅ | ✅ | ✅（python-dotenv 逐级向上找，与路径分隔符无关） |
| 带连字符的主脚本 | ✅ | ✅ | ✅（垫片按文件路径 import，不依赖模块名） |
| compose 卷 `./w-mcp/...:/app` | ✅ | ✅ | 需 Docker Desktop 做路径翻译；NTFS 区分大小写场景注意 |
| 对外暴露 `0.0.0.0` | 首次弹窗放行 | 需放行端口（ufw / firewalld） | 防火墙弹窗需允许 |
| 文件名大小写 | APFS 默认不敏感 | 敏感 | 不敏感 → 代码内路径一律按真实大小写书写 |
| 常驻自启 | Docker Desktop 开 Launch | `systemctl enable docker` | Docker Desktop 开 Start on login |

`third-party/` 下的 Node 服务只保证 Docker 这一条路径（compose 同一条命令，三系统一致）；
想脱离 Docker 跑需自备 Node 20+，属上游仓库支持范围。

## 三、如何用 Docker 起

固定结构：

```
docker/asgi_both.py            # 共用启动垫片（双传输）
<NN_xxx_server>/Dockerfile     # 每个服务一个
<NN_xxx_server>/asgi_both.py   # 垫片的副本（build context 限制，需复制一份）
docker-compose.yml             # 统一管理
.env                           # 密钥，compose 自动读取，勿提交
```

1. 双传输垫片 `docker/asgi_both.py`：
   FastMCP 原生一次只能跑一种传输。垫片取 `mcp._mcp_server` 底层实例，
   用 `StreamableHTTPSessionManager` 处理 `/mcp`，用 `SseServerTransport`
   处理 `/sse` + `/messages/`。`app` 是一个**原生 ASGI 分发函数**（按 path
   直调三个 handler），Starlette 只借它跑 lifespan（`@asynccontextmanager`
   包 `streamable.run()`）和默认 404。
   server 脚本零改动：容器里用 `MCP_MODULE=mcp_server`（或 `MCP_SERVER_FILE=路径`），
   本机调试直接用 CLI：`uv run python asgi_both.py --file w-xxx_mcp_server.py`。
   垫片按**文件路径**加载目标脚本，所以带连字符的文件名也能直接喂进去。

2. Dockerfile 套路（Python 服务）：

   ```dockerfile
   FROM python:3.12-slim
   WORKDIR /app
   RUN pip install --no-cache-dir "mcp>=1.12,<2" python-dotenv requests uvicorn
   COPY w-xxx_mcp_server.py ./mcp_server.py   # 文件名带连字符必须重命名，否则 import 失败
   COPY asgi_both.py ./
   ENV MY_MCP_HOST=0.0.0.0 MY_MCP_PORT=16684 MCP_MODULE=mcp_server
   EXPOSE 16684
   CMD ["sh", "-c", "uvicorn asgi_both:app --host 0.0.0.0 --port ${MY_MCP_PORT}"]
   ```

   Node 服务（如 12306）：用现成镜像 + compose 覆盖 `command` 改端口。

3. compose 关键点：
   - 宿主端口 = 容器端口，同名好记。
   - 密钥注入：`environment: KEY: ${KEY}`（compose 读根目录 `.env`）。
   - 有状态服务挂回宿主（如 `./w-mcp/02_file-mcp-server:/app`）。注意卷会遮蔽镜像内
     代码：代码放 `/opt/app`，`cd /app` 后再起 uvicorn（见 02 的 Dockerfile）。
   - `restart: unless-stopped`。

4. 日常命令：

   ```bash
   cd /Users/will/workspace/mcpserver-dev
   docker compose up -d --build        # 构建并启动全部
   docker compose build mcp-xxx && docker compose up -d mcp-xxx   # 只更新一个
   docker compose logs -f mcp-xxx
   docker compose down                 # 停止并删除容器
   ```

5. 验证清单（新服务上线前逐项过）：
   - `docker ps` 状态 Up 且不重启。
   - `/mcp`：initialize → notifications/initialized → tools/list → tools/call
     完整会话（Streamable HTTP 需要回传 `mcp-session-id` 头）。
   - `/sse`：`GET /sse` 拿 endpoint → `POST /messages/` → SSE 流收到 serverInfo。
   - 至少一次真实工具调用返回真实数据。
   - 客户端断连后日志干净：`curl -m 1 -N http://127.0.0.1:<port>/sse` 连打几次，
     `docker logs mcp-xxx` 不应出现 ASGI/traceback。
   - 端口排查：`lsof -nP -iTCP:<port> -sTCP:LISTEN`（宿主残留进程会抢 IPv4，
     Docker 只占 IPv6，表现为端口通但响应来自旧服务）。

## 四、踩坑记录（勿重蹈）

- Starlette `Mount("/mcp")` 的匹配规则是 `/mcp/...`，裸 `/mcp` 会被 307
  重定向到 `/mcp/`，而多数 MCP 客户端不跟重定向 → 不要用 Mount 挂 `/mcp`。
- Starlette `Route(endpoint=...)` 会把 handler 返回值当 Response 再发一次：
  SSE/流式响应结束后 uvicorn 报
  `Unexpected ASGI message 'http.response.start' sent, after response already completed`
  （每次客户端断连刷一条，客户端不受影响但日志很脏）→ 用原生 ASGI 函数自己分发。
- 新版 starlette（容器内 1.9.0）`Starlette()` 没有 `redirect_slashes` 参数
  （在 `Router()` 上），别指望用 `redirect_slashes=False` 绕开上面的坑。
- 垫片已同时兼容 `/mcp` 与 `/mcp/`，未知路径统一 404。
- 带连字符的主脚本（`w-xxx_mcp_server.py`）能直接 `python` 运行，但**不能被 import**
  （模块名不允许连字符）→ 容器里 COPY 成 `mcp_server.py`，本机用垫片的 `--file`
  走 `spec_from_file_location` 按路径加载。
- uv 用项目模式而不是 `--no-project --with ...`：加 `pyproject.toml` 后命令更短，
  且 `uv.lock` 能锁定三系统一致的依赖；记得忽略 `.venv/`、提交 `uv.lock`。
- PowerShell 没有 `VAR=value cmd` 前缀语法 → 跨平台命令一律靠 CLI 参数传配置。
- `StreamableHTTPSessionManager.run()` 不接受 app 参数，lifespan 需自己包
  `@asynccontextmanager`。
- `.env` 给 compose 用的行避免空格式差异：`KEY=value`，值不带引号最稳。
- 局域网访问要追加 Host 白名单：`WEATHER_MCP_ALLOWED_HOSTS` /
  `FILE_MCP_ALLOWED_HOSTS`（12306 无 Host 校验，无需配置）。

## 五、客户端接入

统一模板（`<name>`/`<port>` 按表替换）：

```json
{
  "mcpServers": {
    "w-OpenWeatherMCP": {
      "name": "w-OpenWeatherMCP",
      "description": "实时天气与 5 天/3 小时预报（OpenWeather）",
      "baseUrl": "http://127.0.0.1:16681/mcp",
      "isActive": true
    }
  }
}
```

- Cherry Studio：Streamable HTTP 用 `baseUrl`；SSE 回退改为
  `"url": "http://127.0.0.1:<port>/sse"`。
- 通用客户端：多数只认 `url` 字段，去掉 name/description/isActive 亦可。
