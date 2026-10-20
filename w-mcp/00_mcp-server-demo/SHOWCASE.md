[English](../../../english/02_mcp/cases/00_mcp-server-demo/SHOWCASE.md) | 简体中文

# w-MCP Demo 与 w-Time MCP 演示

以下是协议测试覆盖的可复现流程，不是编造的真实对话记录。保留 HTTP 与 stdio 两种教学示例。

## HTTP：工具、资源与提示

启动 `python w-mcp_demo_server.py`，连接配置的 MCP URL。

| 步骤 | 调用 | 预期 |
| --- | --- | --- |
| 健康检查 | ping() | server=w-MCPDemo，transport=streamable-http |
| 加法 | add(a=-1,b=5) | 4 |
| 默认资源 | greeting://Alice | Hello, Alice! |
| 中文资源 | greeting://Alice/zh | 保留姓名 Alice 的中文问候 |
| 英文提示 | greet_user(name="Alice",language="en") | 英文写作指令 |
| 中文提示 | greet_user(name="Alice",language="zh") | 中文写作指令 |
| 无效姓名 | 纯空白或超过 200 字符 | 校验错误 |

未知风格回退 friendly。prompt 返回模型写作指令，不是生成后的问候。真实 HTTP 测试覆盖加法、中英文资源与提示。

## stdio：明确时区与时间转换

客户端通过 Python 绝对路径启动 `w-time_mcp_server.py`。stdio 是协议通道，普通日志不要写 stdout。

```text
get_current_time(timezone_name="UTC", language="en")
get_current_time(timezone_name="Asia/Shanghai", language="zh")
convert_time(iso_time="2026-10-17T12:00:00+08:00", to_timezone="UTC")
```

转换结果为 `2026-10-17T04:00:00+00:00`。当前时间返回带偏移 ISO-8601、Unix 时间戳与 UTC 偏移，同时保留 current_time。
未指定时区时使用服务器本地时区；IANA 时区需要系统数据库或 tzdata。

无偏移时间必须提供 from_timezone。无效时区、缺少源时区、显式偏移与源时区冲突、夏令时歧义或不存在时刻，都返回本地化错误和稳定错误码。
单元测试覆盖这些边界；真实 stdio 客户端覆盖两种语言的当前时间与转换，不猜测客户端时区。

## 验证

在父级 mcp 目录，按 README 安装依赖后运行：

```bash
python -B -m unittest -v test_weather_demo test_bilingual_tools
```

本次平台为 Windows/Python 3.12，未在 Linux/macOS 实测。旧入口已移除，原启动配置需更新。
