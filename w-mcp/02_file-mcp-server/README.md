[English](../../../english/02_mcp/cases/02_file-mcp-server/README.md) | 简体中文

# w-File Workbench MCP

服务名称统一为 **w-File Workbench MCP**，MCP 标识为 `w-FileWorkbenchMCP`，正式入口为 `w-file_workbench_mcp_server.py`。
仅保留上述正式入口；旧启动命令需改为使用该脚本。
`w` 是作者名字缩写；本项目后续新增 MCP 服务名和主脚本统一以 `w-` 开头，约定已记录在根目录 `AGENTS.md`。
带连字符的主脚本可直接运行；程序加载可使用 `importlib.util.spec_from_file_location`。
现有 `FILE_MCP_*` 环境变量、16682 端口、`secure_rename_history.json` 数据文件及 `02_file-mcp-server` 编号目录保持兼容。
升级后重启服务并刷新客户端工具列表即可。

`w-FileWorkbenchMCP` 是一个基于 FastMCP 的文件操作 MCP 服务。它面向课堂演示、本机实验或可信内网 Agent 场景，提供文件列表、文本读取、文件信息查询、创建文件夹、创建文本文件、同目录重命名和重命名恢复能力。

正式入口脚本：

```text
w-file_workbench_mcp_server.py
```

默认 MCP 地址：

```text
http://<服务器IP>:16682/mcp
```

## 功能定位

该服务适合作为“文件系统能力如何封装为 MCP 工具”的课堂示例：

- 默认允许访问服务账号有权限读取的任意路径
- 默认监听 `0.0.0.0:16682`，方便远端 MCP 客户端连接
- 默认关闭 Host/Origin 校验，避免远端客户端因 `Host` 头返回 421
- 不设文件系统根目录白名单，访问范围由服务进程的系统账号权限决定
- 使用真实路径解析
- 限制单次文件读取大小，避免撑爆上下文
- 创建文本文件默认不覆盖已有文件
- 文件重命名不覆盖目标文件
- 重命名历史使用原子写入
- 返回结构化结果，便于 Agent 判断成功或失败
- Windows、macOS、Linux 均可使用

## 文件说明

| 文件 | 说明 |
| --- | --- |
| `w-file_workbench_mcp_server.py` | 正式 MCP 服务入口，推荐使用 |
| `files/` | a.txt 至 e.txt 的源码示例，供课堂和 showcase 使用 |
| `secure_rename_history.json` | 正式服务运行后自动生成的重命名历史文件 |

## 环境要求

在本模块环境中安装依赖：

```bash
python -m pip install "mcp>=1.12,<2" python-dotenv requests
```

主要依赖：

- `mcp.server.fastmcp.FastMCP`
- `python-dotenv`
- Python 标准库文件 API

## 安全结论

当前默认配置是“可信环境优先”的教学配置，不是生产安全配置。

默认启动后，任何能访问 `http://<服务器IP>:16682/mcp` 的客户端，都可以通过 MCP 工具执行以下操作：

- 列出服务账号有权限访问的目录
- 读取服务账号有权限访问的文本文件
- 创建文件夹
- 创建文本文件
- 在传入 `overwrite=true` 时覆盖已有文本文件
- 重命名普通文件
- 查看和恢复本服务记录的最近一次重命名

因此，公网或多人共用服务器上不要用高权限账号运行本服务。至少应使用低权限专用账号，并通过安全组、防火墙或内网访问控制限制来源 IP。

### 风险清单

| 风险 | 当前默认行为 | 建议 |
| --- | --- | --- |
| 未鉴权访问 | 服务自身不校验用户身份 | 只在可信网络使用，或放在带鉴权的网关/反向代理后 |
| 远程任意客户端可连 | 默认监听 `0.0.0.0` | 用安全组/防火墙限制访问来源；本机使用时设 `FILE_MCP_HOST=127.0.0.1` |
| Host/Origin 不校验 | 默认关闭 DNS rebinding 防护 | 需要防护时启用 `FILE_MCP_ENABLE_DNS_REBINDING_PROTECTION=true` |
| 读写范围过大 | 服务不设根目录限制 | 使用低权限专用系统账号运行，通过操作系统权限控制可访问范围 |
| 写文件风险 | `create_file` 可创建文件，`overwrite=true` 可覆盖文本文件 | 不要把服务暴露给不可信客户端；用低权限账号运行 |
| 路径误解 | MCP 只能访问服务所在机器文件系统 | Windows 客户端路径如 `E:\test` 只有在服务运行于 Windows 或目录被挂载到服务端时才可访问 |
| 大目录响应过大 | `list_files` 支持 pattern、limit、offset | 默认最多返回 200 项，使用 next_offset 继续；枚举仍需扫描目录 |
| 历史文件泄露路径 | `secure_rename_history.json` 会记录重命名的绝对路径 | 不要把历史文件提交到公开仓库；必要时定期清理 |

### 推荐部署方式

课堂演示或个人实验，追求省事：

```env
FILE_MCP_HOST=0.0.0.0
FILE_MCP_PORT=16682
```

只允许本机客户端访问：

```env
FILE_MCP_HOST=127.0.0.1
FILE_MCP_PORT=16682
```

需要启用 Host/Origin 校验：

```env
FILE_MCP_ENABLE_DNS_REBINDING_PROTECTION=true
FILE_MCP_ALLOWED_HOSTS=8.159.144.108:16682,127.0.0.1:16682,localhost:16682
```

生产或公网环境建议不要裸跑本服务。更合理的做法是：

- 放在 VPN、内网或堡垒机后面
- 使用 Nginx / API 网关 / MCP 客户端侧能力做身份认证
- 使用独立低权限系统账号运行
- 用操作系统权限让运行账号只能访问必要目录
- 定期检查 `secure_rename_history.json`

## 路径访问模型

`w-FileWorkbenchMCP` 不做允许根目录过滤，也不读取根目录白名单环境变量。只要 MCP
服务进程对应的系统账号有权限访问，工具就可以访问对应路径。

路径处理规则：

- 绝对路径会直接解析并访问，不检查允许根目录。
- 未传 `base_dir` 的相对路径默认相对于启动服务时的当前工作目录解析。
- 传入 `base_dir` 时，相对路径会基于 `base_dir` 解析。
- 所有路径都会先经过 `resolve()` 再访问。

## 关闭超时配置

Streamable HTTP 客户端可能会保持 HTTP keep-alive 连接。停止服务时如果看到：

```text
Waiting for connections to close. (CTRL+C to force quit)
```

通常是客户端连接还没完全断开。脚本默认将 uvicorn 的 keep-alive 超时设为 1 秒，
优雅关闭等待设为 2 秒，降低 `Ctrl+C` 后卡住的概率。

如需调整，可以在 `.env` 中配置：

```env
FILE_MCP_UVICORN_TIMEOUT_KEEP_ALIVE=1
FILE_MCP_UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN=2
```

如果仍然卡住，可以再按一次 `Ctrl+C` 强制退出。

## 启动服务

依赖 Python 3.12、`mcp>=1.12,<2`、`python-dotenv`。

### 方式一：现有环境直接运行

```bash
python w-file_workbench_mcp_server.py
```

### 方式二：uv 临时环境免安装启动（课堂推荐）

```bash
uv run --no-project --with "mcp>=1.12,<2" --with python-dotenv \
  python w-file_workbench_mcp_server.py
```

### 方式三：uv 固定虚拟环境

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python "mcp>=1.12,<2" python-dotenv
uv run --python .venv/bin/python w-file_workbench_mcp_server.py
```

启动后 MCP 客户端连接：

```text
http://<服务器IP>:16682/mcp
```

## 远端访问配置

服务默认监听 `0.0.0.0:16682`，并默认关闭 Host/Origin 校验，方便远端客户端直接访问。

例如服务公网地址是 `8.159.144.108:16682`，客户端 MCP URL 配置为：

```text
http://8.159.144.108:16682/mcp
```

如需修改端口：

```env
FILE_MCP_PORT=16682
```

如需只监听本机：

```env
FILE_MCP_HOST=127.0.0.1
```

如果需要重新启用 Host/Origin 校验，可以配置：

```env
FILE_MCP_ENABLE_DNS_REBINDING_PROTECTION=true
FILE_MCP_ALLOWED_HOSTS=8.159.144.108:16682,127.0.0.1:16682,localhost:16682
```

启用校验后，如果日志中出现：

```text
Invalid Host header: 8.159.144.108:16682
421 Misdirected Request
```

说明客户端请求的 `Host` 不在 `FILE_MCP_ALLOWED_HOSTS` 中，把日志里的完整 Host 值加入即可。

## 在 Agent 中配置

服务是常驻 HTTP 进程，先启动再连接。它能读写本机文件，请把可访问范围限制在可信目录，
并优先用 `FILE_MCP_HOST=127.0.0.1` 只监听本机。

### Codex

一行添加，`codex mcp list` 校验：

```bash
codex mcp add w-FileWorkbenchMCP --url http://127.0.0.1:16682/mcp
codex mcp list
```

或直接写 `~/.codex/config.toml`：

```toml
[mcp_servers.w-FileWorkbenchMCP]
url = "http://127.0.0.1:16682/mcp"

# 课堂只读演示时收敛工具面
enabled_tools = ["ping", "list_files", "read_file", "get_file_info"]
```

需要放开写入类工具时再删除 `enabled_tools`；写类工具的审批粒度可用
`default_tools_approval_mode = "prompt"` 控制。改完配置需重启 Codex。

### Cherry Studio

界面路径：设置 → MCP 服务 → 添加服务器 → 类型选 **Streamable HTTP** →
服务器地址填 `http://127.0.0.1:16682/mcp` → 保存后打开开关，并在 Agent 中勾选。

JSON 形式（Cherry Studio 的远程地址字段是 `baseUrl`，旧版本兼容 `url`）：

```json
{
  "mcpServers": {
    "w-FileWorkbenchMCP": {
      "name": "w-FileWorkbenchMCP",
      "description": "本地文件读取、列表、重命名 MCP 服务",
      "type": "streamableHttp",
      "baseUrl": "http://127.0.0.1:16682/mcp",
      "isActive": true
    }
  }
}
```

## 工具列表

### `ping()`

健康检查工具。

返回内容包括：

- 服务名
- 当前平台
- 当前监听地址和端口
- 路径分隔符
- 环境变量路径列表分隔符
- 文件系统访问模式（`unrestricted_by_server`）
- 是否启用 Host/Origin 校验
- 当前允许的 Host/Origin 列表

### `list_allowed_roots()`

为兼容旧客户端保留的诊断工具。`allowed_roots` 始终为空列表，并明确返回服务未设根目录限制。

排查路径访问行为时，建议先调用该工具。

### `list_files(directory=".", base_dir="")`

列出目录下的文件和子目录。

返回每个条目的基础元信息，包括：

- 名称
- 路径
- 是否文件
- 是否目录
- 文件大小
- 修改时间
- 访问时间
- 创建时间或元数据变更时间

### `read_file(filepath, base_dir="")`

读取文本文件内容。

读取策略：

- 默认可读取服务账号有权限访问的普通文件
- 默认最多读取 `FILE_MCP_MAX_READ_BYTES` 字节
- 优先使用 `utf-8` 解码
- 失败后尝试 `gb18030`
- 二进制文件或未知编码会返回结构化错误

### `get_file_info(filepath, base_dir="")`

查看文件或目录元信息。

适合在读取或重命名前确认目标路径是否存在、类型是否正确。

### `create_directory(directory, base_dir="", parents=true)`

创建文件夹。

参数说明：

- `directory`：要创建的目录路径，可以是绝对路径，也可以配合 `base_dir` 使用相对路径。
- `base_dir`：可选基础目录。
- `parents`：是否递归创建父目录，默认 `true`。

行为说明：

- 目录已存在时返回成功，并标记 `created=false`。
- 目标路径已存在但不是目录时返回失败。

### `create_file(filepath, content, base_dir="", overwrite=false, create_parent_dirs=true)`

创建文本文件并写入内容。

参数说明：

- `filepath`：要创建的文件路径，可以是绝对路径，也可以配合 `base_dir` 使用相对路径。
- `content`：文件内容，使用 `utf-8` 编码写入。
- `base_dir`：可选基础目录。
- `overwrite`：是否覆盖已有文件，默认 `false`。
- `create_parent_dirs`：父目录不存在时是否自动创建，默认 `true`。

行为说明：

- 默认不覆盖已有文件；如需覆盖，设置 `overwrite=true`。
- 目标路径是目录时返回失败。
- 该工具只适合写文本内容，统一使用 `utf-8` 编码。

### `rename_file(old_path, new_name, base_dir="")`

在同一目录内重命名文件。

安全策略：

- 不允许跨目录移动
- 不允许覆盖已有目标文件
- `new_name` 只能是文件名，不能包含路径
- 拒绝 Windows 不兼容文件名和非法字符，例如：
  - `CON`
  - `PRN`
  - `COM1`
  - `<>:"/\\|?*`
  - 结尾空格
  - 结尾句点

成功重命名会写入：

```text
secure_rename_history.json
```

### `view_rename_history(filter_dir="")`

查看本服务记录的重命名历史。

可选传入 `filter_dir`，只查看指定目录下的历史记录。

### `restore_last_rename()`

恢复最近一次由本服务执行的重命名。

该工具只恢复最后一条历史记录，不做批量恢复，目的是降低误操作风险。

## 响应格式

成功响应：

```json
{
  "ok": true
}
```

失败响应：

```json
{
  "ok": false,
  "error": "error_code",
  "message": "错误说明"
}
```

这种结构比纯文本更适合 Agent 稳定判断下一步动作。

## 跨平台说明

- Windows、macOS、Linux 均可使用。
- Windows 上可以直接访问本机绝对路径，例如 `E:\test`，但仍然只能访问 MCP 服务所在机器的文件系统。
- MCP 服务只能访问“运行服务的那台机器”的文件系统。例如服务运行在 macOS/Linux 上时，
  传入 `E:\test` 不会访问 Windows 客户端电脑的 E 盘；请在 Windows 机器上启动本服务，
  或先把该目录挂载到服务所在机器后使用挂载路径。
- macOS 中 `/tmp` 可能经 `resolve()` 解析为 `/private/tmp`。
- 文件名校验采用 Windows 兼容策略，即使在 macOS/Linux 上也会拒绝 Windows 保留名，方便文件跨平台流转。

## 常见问题

### Agent 说创建成功，但实际文件不存在

不要只看模型口头回复。正确排查方式：

1. 先调用 `ping()`，确认连接的是预期那台 MCP 服务。
2. 调用 `create_file()` 后检查返回 JSON 中的 `ok` 和 `path`。
3. 立刻调用 `read_file()` 读取同一路径，确认内容真的存在。
4. 如果路径是客户端本机路径，确认 MCP 服务是否也运行在同一台机器。

示例：

```json
{
  "filepath": "/Users/will/workspace/tmp.py",
  "content": "print(\"hello world\")\n",
  "overwrite": true
}
```

随后验证：

```json
{
  "filepath": "/Users/will/workspace/tmp.py"
}
```

### CherryAI 中传入 `E:\test`，返回无法访问

先调用 `ping()` 查看 `platform`。

如果 `platform` 不是 `Windows`，说明文件 MCP 服务没有运行在 Windows 本机。
此时 `E:\test` 是客户端电脑路径，不是服务端路径，服务无法直接访问。

跨机器场景的解决方式：

1. 在 Windows 本机启动 `w-file_workbench_mcp_server.py`。
2. 或者把 Windows 目录共享/挂载到服务所在机器，再使用挂载后的 macOS/Linux 路径。

## 验证

语法检查：

```bash
python -m py_compile w-file_workbench_mcp_server.py
```

基础验证流程：

1. 启动服务。
2. 调用 `ping()`。
3. 调用 `list_allowed_roots()`。
4. 调用 `list_files(".")`。
5. 创建一个测试文件夹。
6. 创建一个小文本文件。
7. 读取这个小文本文件。
8. 重命名一个测试文件。
9. 调用 `view_rename_history()`。
10. 调用 `restore_last_rename()`。

## 当前限制

- 提供普通文件的可恢复删除，不提供目录删除或永久删除。
- 不提供递归搜索和全文搜索。
- 大目录支持分页返回，但尚未提供索引式查询。
- 不提供内置用户鉴权、权限分级和操作审批。
- 不提供二进制文件写入能力。
- 人工审批应放在 MCP 客户端或 Agent 中间件层处理。

## 中英文场景

服务说明与工具描述中英双语（中文在前、英文在后）。服务生成的提示默认使用英文，工具参数 `language="zh"` 切换为中文，
`language="en"` 显式使用英文。工具名称、JSON 字段名和错误码保持稳定。
文件名、路径、文件内容及第三方原始数据保持原文，不会自动翻译。
底层操作系统或第三方库的原始异常信息可能使用其自身语言。

请在 MCP 客户端刷新工具列表，以获取新增的可选参数。
已有调用不需要增加参数，但默认提示语言会变为英文。

## 可恢复删除与实测案例

- `delete_file(filepath, base_dir="", language="en")`：先备份再删除单个普通文件；返回 `deletion_id`。拒绝目录和符号链接，不执行通配符或递归删除。
- `restore_deleted_file(deletion_id, language="en")`：按记录 ID 恢复原路径，拒绝覆盖已有文件；父目录必须存在。
- 备份位于服务脚本旁的 `.deleted_files/<deletion_id>/`，不是系统回收站；恢复后仍保留备份，管理员需自行管理容量和权限。备份不应提交到 Git。
- `create_file` 精确保留传入的 LF/CRLF；先编码并写入临时文件，再原子替换或以不覆盖方式发布，编码或暂存失败不会清空原文件。

重启服务并刷新客户端工具列表后，新工具才会出现在现有连接中。
详细对话过程、实际工具调用、问题复盘与后续改进见 [Showcase](SHOWCASE.md)。
新增测试：`python -B test_file_lifecycle.py`，只使用临时目录。

## 可靠性与新增工具

所有工具调用通过线程锁及操作系统文件锁串行执行；共享同一状态目录的多个服务进程也会互斥。
损坏的重命名历史会报错并保留。改名后历史写入失败会尝试回滚，回滚失败则明确返回实际路径和部分成功状态。
撤销改名会核验文件指纹，拒绝处理被修改或替换的文件。旧历史没有文件指纹时仅可查询，不能自动撤销；旧删除备份没有校验和时需要人工恢复。

删除先将源文件暂存到原目录的隔离名称，备份并核验后才删除隔离文件；检测到文件变化会取消并回滚。回滚冲突时返回保留的 `quarantine_path`。
恢复删除会先暂存、核验哈希，再以不覆盖方式发布，失败不会留下半截目标文件。

| 工具或参数 | 行为 |
| --- | --- |
| `list_files(pattern="*", limit=200, offset=0)` | 按文件名筛选、分页，返回 total 和 next_offset；limit 为 1..1000 |
| `read_file(offset=0, max_bytes=0, encoding="auto")` | 按字节分段读取；auto、utf-8、gb18030；使用返回的 encoding 和 next_offset 继续，避免切断字符 |
| `batch_rename_files(renames, base_dir="", dry_run=true, expected_plan_id="")` | 预览 1..100 项改名；每项包含 old_path/new_name；拒绝重名、交换名称和链式改名 |
| `list_deleted_files(filter_dir="", limit=100, offset=0)` | 查询删除记录 ID、状态及损坏记录诊断 |
| `cleanup_backups(older_than_days=30, dry_run=true, expected_plan_id="")` | 按保留天数预览并清理已完成删除备份和快照；跳过 pending/failed 删除记录 |
| `create_directory_snapshot(directory, base_dir="")` | 保存递归普通文件内容、哈希及空目录结构 |
| `list_directory_snapshots()` | 查询快照 ID 和源目录 |
| `restore_directory_snapshot(snapshot_id, dry_run=true, expected_plan_id="")` | 预览并恢复目录，核验文件集合、目录结构和 SHA-256 |

工具均支持 `language="en"` 或 `"zh"`，新参数建议按名称传入，以兼容旧调用。
分页只限制响应大小，仍需枚举目录；读取偏移量以字节计，任意偏移可能切断字符。
`max_bytes=0` 使用配置上限，单次仍不超过 `FILE_MCP_MAX_READ_BYTES`；上限不足以容纳一个字符时返回 `read_limit_too_small`。

批量改名、备份清理和快照恢复默认只预览。应用时传入 `dry_run=false` 及预览返回的 `expected_plan_id`；计划变化会拒绝执行。
批量改名中途失败会返回已完成项，之前成功的改名仍保留，并分别有撤销记录。
快照恢复会先暂存所有需要的快照数据，再备份当前新增或变化的文件；失败返回已完成操作及删除备份 ID，不承诺整目录事务。
快照恢复可移除多余的空目录；不支持符号链接、目录联接及与服务备份目录重叠的根目录，原根目录必须存在。
清理只永久删除选中的备份，不删除源文件。

| 环境变量 | 默认值 | 作用 |
| --- | --- | --- |
| `FILE_MCP_STATE_DIR` | 服务脚本目录 | 历史、锁、删除备份及快照位置；推荐使用独立可写目录 |
| `FILE_MCP_MAX_BACKUP_BYTES` | 1073741824 | 备份共享容量预算；不足时拒绝新增备份 |
| `FILE_MCP_MAX_SNAPSHOT_ENTRIES` | 10000 | 快照遍历的最大文件及目录条目数 |

外部程序不参与服务锁，仍需避免其他程序持有文件句柄同时写入。进程崩溃可能留下暂存文件、隔离文件或 pending 记录；当前不承诺崩溃事务及完整元数据恢复。
实际 HTTP 中英文测试：`python -B test_file_http.py`。覆盖说明见 [TESTING.md](../../../english/02_mcp/cases/02_file-mcp-server/TESTING.md)。

```json
{"filepath": "notes/meeting.txt", "content": "Meeting notes"}
```

```json
{"filepath": "资料/会议记录.txt", "content": "会议记录", "language": "zh"}
```

`read_file` 继续支持 UTF-8 和 GB18030；字节上限截断多字节字符时，
只省略末尾不完整的字符，不会误判为其他编码。
