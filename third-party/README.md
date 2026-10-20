# third-party

公网下载的第三方 MCP Server 源码，非本仓库开发内容。

- `12306-mcp/`：上游 https://github.com/Joooook/12306-mcp 的完整 clone
  （含 .git 与 node_modules），已在根 `.gitignore` 中排除、不随本仓库分发。
  自行 clone 后，容器运行不依赖该目录（compose 使用镜像 `12306-mcp:latest`，
  源自上游 Dockerfile `npm install 12306-mcp`）。
