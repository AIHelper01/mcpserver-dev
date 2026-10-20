# mcpserver-dev — MCP Server 开发基地

本目录用于开发、容器化和运行 MCP Server。
自研 server 放 `w-mcp/`，下载的第三方 server 源码放 `third-party/`。
新增自研 server 请照本指南的既有模式落地。

## 现有服务

目录分两类：`w-mcp/` = 自研；`third-party/` = 公网下载的第三方源码（非本仓库开发，只做容器化运行）。

| 目录 | 服务 | 容器 | 端口 | 端点 |
|---|---|---|---|---|
| w-mcp/00_mcp-server-demo | 教学 demo（ping/时间） | 未容器化 | 8000 | 本机直跑 |
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
python w-xxx_mcp_server.py                       # 直跑（仅 /mcp）
npx @modelcontextprotocol/inspector              # 官方调试器
curl -X POST http://127.0.0.1:<port>/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}'
```

## 二、如何用 Docker 起

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
   用 `StreamableHTTPSessionManager` 精确挂 `/mcp`，
   用 `SseServerTransport` 挂 `/sse` + `/messages/`，
   lifespan 用 `@asynccontextmanager` 包 `streamable.run()`。
   server 脚本零改动，只需 `MCP_MODULE` 指向模块名。

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
   - 端口排查：`lsof -nP -iTCP:<port> -sTCP:LISTEN`（宿主残留进程会抢 IPv4，
     Docker 只占 IPv6，表现为端口通但响应来自旧服务）。

## 三、踩坑记录（勿重蹈）

- 模块名/文件名不能带连字符：COPY 时重命名为 `mcp_server.py`。
- Starlette `Mount("/mcp")` 会 307 重定向到 `/mcp/`，多数 MCP 客户端不跟
  重定向 → 用精确 `Route("/mcp")` 直调 session manager。
- `StreamableHTTPSessionManager.run()` 不接受 app 参数，lifespan 需自己包
  `@asynccontextmanager`。
- `.env` 给 compose 用的行避免空格式差异：`KEY=value`，值不带引号最稳。
- 局域网访问要追加 Host 白名单：`WEATHER_MCP_ALLOWED_HOSTS` /
  `FILE_MCP_ALLOWED_HOSTS`（12306 无 Host 校验，无需配置）。

## 四、客户端接入

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
