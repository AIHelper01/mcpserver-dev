[English](../../../english/02_mcp/cases/02_file-mcp-server/SHOWCASE.md) | 简体中文

# 用自然语言整理源码文件：w-File Workbench MCP 对话实测

测试日期：2026-10-17。客户端：Codex 桌面端；服务：`local_file_mcp`，实现入口为 `w-file_workbench_mcp_server.py`。测试目录为 Windows 本机 `D:\files`。

服务现名为 w-File Workbench MCP（`w-FileWorkbenchMCP`）。原对话时入口为 `secure_file_mcp_server.py`，现已移除，请使用当前正式入口；`local_file_mcp` 是客户端连接别名。

## 初始数据

目录中只有以下五个 UTF-8 文本文件，没有子目录。内容是文件操作代码，而非普通文章。

| 文件 | 原始大小 | 内容 |
| --- | ---: | --- |
| a.txt | 288 字节 | Python 读取、覆盖写入、追加 |
| b.txt | 411 字节 | Go 创建文件、缓冲写入 |
| c.txt | 533 字节 | Java 创建文件并写入，公共类为 Main |
| d.txt | 218 字节 | C 使用 fopen、fprintf、fclose 写文件 |
| e.txt | 293 字节 | Node.js 使用 fs 异步写入和读取 |

## 原始对话与实际执行

| 步骤 | 用户指令 | 实际工具与结果 |
| --- | --- | --- |
| 1 | D:\files 下有哪些文件，用 MCP 来执行 | `list_files` 返回 a.txt 到 e.txt |
| 2 | 根据他们的内容，给出合适的命名建议 | 五次 `read_file`，识别语言和用途；只建议，不改名 |
| 3 | 修改 | 五次 `rename_file`，使用下表中的描述性名称 |
| 4 | 把各文件的文件类型也改下 | 五次 `rename_file`，扩展名改为源码类型；Java 使用 Main.java |
| 5 | 可以新增一个 ts 语言的例子吗，新增一个文件 | `create_file` 创建 TypeScript 示例，`read_file` 回读核实 |
| 6 | 删除文件夹下的 go 文件 | 当时 MCP 缺少删除工具，实际使用 PowerShell Remove-Item；Test-Path 返回 False |
| 7 | 恢复这个文件夹为初始的样子 | MCP 还原四个名称并重建 b.txt；PowerShell 删除新增 TS 文件及修正 b.txt 换行 |

### 命名映射

| 初始 | 内容命名后 | 源码扩展名调整后 |
| --- | --- | --- |
| a.txt | Python_文件读取写入与追加.txt | Python_文件读取写入与追加.py |
| b.txt | Go_文件创建与缓冲写入.txt | Go_文件创建与缓冲写入.go |
| c.txt | Java_新建文件并写入.txt | Main.java |
| d.txt | C_文件创建与写入.txt | C_文件创建与写入.c |
| e.txt | NodeJS_异步文件读写.txt | NodeJS_异步文件读写.js |

修改扩展名不会编译、转换或运行代码。Main.java 的命名来自源文件的 `public class Main`。

新增文件为 `TypeScript_文件读取写入与追加.ts`，使用 `node:fs` 的 `writeFileSync`、`appendFileSync`、`readFileSync` 顺序操作 `typescript.txt`，有显式 string 类型。只创建和回读，没有运行这些示例。

### 实测暴露的问题

1. **工具缺口**：删除必须退出 MCP，无法完成纯 MCP 闭环。
2. **恢复能力有限**：`restore_last_rename` 只能撤销重命名，不能恢复删除、新增和内容覆盖。本次 Go 文件是根据对话中读出的内容重建，并非从备份恢复。
3. **换行保真问题**：Windows 默认文本写入再次转换传入的 CRLF，使重建 b.txt 从原来的 411 字节变成 431 字节；修正重复 CR 后回到 411 字节。
4. **验证边界**：最终目录确认为五个原名文件，Go 文件大小恢复为 411 字节；当时没有执行五个文件的整体 SHA-256 比对，不能据此宣称所有元数据及内容均已严格恢复。

## 本次补齐后的 MCP 流程

重启服务并在客户端刷新工具列表后，可以使用新增工具。下面是复现步骤，**不是原始会话已调用过这些新工具的记录**。

```text
list_files(directory="D:\\files")
read_file(filepath="D:\\files\\b.txt")
rename_file(old_path="D:\\files\\b.txt", new_name="Go_文件创建与缓冲写入.go")
delete_file(filepath="D:\\files\\Go_文件创建与缓冲写入.go")
# 保存返回的 deletion_id
restore_deleted_file(deletion_id="上一步返回的32位ID")
restore_last_rename(filter_dir="D:\\files")
list_files(directory="D:\\files")
```

删除现在先将源目录项移至隔离名称，再于 `FILE_MCP_STATE_DIR/.deleted_files/<deletion_id>/` 保存并核验原始字节及支持的文件属性，最后移除隔离文件；检测到变化会取消删除。恢复先暂存和校验，再以不覆盖方式发布。该目录是服务私有备份，不是操作系统回收站；`cleanup_backups` 可预览并永久清理过期已完成备份。未增加独立的递归目录删除工具。

完整重演时，应在临时目录复制五个测试文件，记录初始名称及 SHA-256；每一步检查工具返回的 `ok`，最后检查恰好只有 a.txt 至 e.txt 且哈希相同。新增 TS 文件可以通过 `delete_file` 清理。不要把对话内容当作可靠备份。

## 实测后已完成的改进

| 方向 | 当前实现 |
| --- | --- |
| 目录快照 | 创建和查询快照、预览恢复、要求当前计划 ID、最终核验文件名、目录结构及哈希 |
| 历史与并发 | 线程及操作系统文件锁保护共享状态；原子历史写入、损坏报告、身份核验和历史写入失败回滚 |
| 删除记录与保留策略 | 分页查询、容量预算、保留天数清理预览及显式清理；保留 pending/failed 删除记录 |
| 原子写入及恢复 | 先编码、暂存再发布；备份哈希校验、恢复不覆盖、明确返回部分失败 |
| 批量改名 | 预览并校验 1..100 项，要求当前计划 ID，失败返回已完成项；拒绝交换名称和链式改名 |
| 目录分页 | pattern 筛选，limit/offset/next_offset 分页 |
| 分段读取 | 字节范围读取，显式或自动 UTF-8/GB18030 编码，返回完整字符边界的 next_offset；写入保留换行 |
| 部署 | 保持现有可信环境访问模型，鉴权仍由外部网关或部署环境负责 |

以上改进已在原对话结束后验证，不改变原实测历史。服务锁协调参与的服务调用，不阻止外部程序写文件。目录恢复不是整目录事务；进程中断可能留下暂存、隔离文件及 pending 记录，不承诺完整元数据备份或崩溃事务。验证覆盖见 [TESTING.md](../../../english/02_mcp/cases/02_file-mcp-server/TESTING.md)。

## 本地回归

在本目录执行：

```bash
python -B test_file_lifecycle.py
python -B test_file_http.py
```

在父级 mcp 目录执行已有回归：

```bash
python -B test_bilingual_tools.py
```

测试使用临时目录，不修改真实 D:\files。覆盖中英文场景、实际 HTTP MCP 分发、换行保真、二进制恢复、写入中断、备份损坏、历史失败、线程及进程并发、保留策略清理，以及改名、新增、删除和修改后的完整快照恢复。
