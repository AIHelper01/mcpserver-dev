# third-party

公网下载的第三方 MCP Server 源码，非本仓库开发内容。

- `12306-mcp/`：上游 https://github.com/Joooook/12306-mcp 的完整 clone
  （含 .git 与 node_modules），已在根 `.gitignore` 中排除、不随本仓库分发。
  自行 clone 后，容器运行不依赖该目录（compose 使用镜像 `12306-mcp:latest`，
  源自上游 Dockerfile `npm install 12306-mcp`）。

## 运行与跨平台

- 统一走 Docker：仓库根执行 `docker compose up -d mcp-12306`，
  macOS / Linux / Windows（Docker Desktop）命令一致，不需要本机装 Node。
- 上游自带 `/mcp`（Streamable HTTP）与 `/sse`，容器端口 16683，无 Host 校验。
- 想脱离 Docker 直跑需自备 Node 20+（`npx 12306-mcp`），属上游支持范围，
  本仓库不额外维护其依赖与跨平台差异。
