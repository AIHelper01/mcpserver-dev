# w-OpenWeather MCP

[English](../../../english/02_mcp/cases/01_weather-mcp-server/README.md) | 简体中文

正式入口仅保留 `w-openweather_mcp_server.py`，服务标识为 `w-OpenWeatherMCP`。
不保留旧启动脚本；现有 API key 环境变量别名继续兼容。

## 安装与启动

依赖 Python 3.12、`mcp>=1.12,<2`、`python-dotenv`、`requests`。

### 方式一：pip 装进现有环境

```bash
python -m pip install "mcp>=1.12,<2" python-dotenv requests
python w-openweather_mcp_server.py
```

### 方式二：uv 临时环境免安装启动（课堂推荐）

```bash
uv run --no-project \
  --with "mcp>=1.12,<2" --with python-dotenv --with requests \
  python w-openweather_mcp_server.py
```

uv 的临时环境不写入仓库，也不会污染当前 Python；首次执行会下载依赖，之后走缓存。

### 方式三：uv 固定虚拟环境

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python "mcp>=1.12,<2" python-dotenv requests
uv run --python .venv/bin/python w-openweather_mcp_server.py
```

### 密钥与地址

在环境变量或 `.env` 配置 `OPENWEATHER_API_KEY`，旧名 `API_KEY_OPENWEATHER` 作为回退。
`.env` 从脚本所在目录逐级向上查找，放在仓库根目录即可被找到。
不要提交密钥。`ping`、`check_config` 不返回密钥。客户端连接 `http://127.0.0.1:16681/mcp`。

## 工具与语言

| 工具 | 行为 |
| --- | --- |
| `ping()` | 检查服务及传输配置，不访问天气 API |
| `check_config(language="en")` | 检查密钥是否存在及接口格式，不验证真实密钥有效性 |
| `get_weather(location, language="en", include_raw=false)` | 按城市查询当前天气，可使用 city,country 消除歧义 |
| `get_weather_by_coordinates(latitude, longitude, language="en", include_raw=false)` | 按坐标查询，纬度 -90..90，经度 -180..180 |
| `get_forecast(location, forecast_date="tomorrow", language="en", include_periods=true, include_raw=false)` | 按城市查询地点当地日期的预报 |
| `get_forecast_by_coordinates(latitude, longitude, forecast_date="tomorrow", language="en", include_periods=true, include_raw=false)` | 按坐标查询地点当地日期的预报 |

服务说明与工具描述中英双语（中文在前、英文在后）；返回内容默认英文，`language="zh"` 返回中文提示和天气描述。地点内容不翻译，字段与错误码稳定。
城市名会去除首尾空白，最多 200 字符。名称解析取决于服务商覆盖，坐标查询可避开名称歧义。
接口依据 [OpenWeather 官方说明](https://openweathermap.org/api/current)，使用 metric 单位及语言参数；不增加自动地理编码依赖。
温度为摄氏度、风速为米每秒；服务商提供时返回观测时间。

原始响应改为按需返回：设置 `include_raw=true`。即便原始响应反射了密钥，也会脱敏。

## 预报日期与汇总

使用 OpenWeather 的 [5 天 / 3 小时预报 API](https://openweathermap.org/forecast5)，沿用现有 API key；实际接口权限取决于服务商账号。
`forecast_date` 支持 tomorrow（默认）、today、all 或 YYYY-MM-DD。
相对日期根据当前时刻和地点返回的 UTC 偏移计算，不使用服务器本地日期；缺少地点偏移会报错。

汇总温度是返回分时采样的最小值、最大值，不是官方全天极值。
`max_period_precipitation_probability` 是各时段降水概率的最大值，不是全天降雨概率。
`has_eight_3h_samples` 标记是否有连续八个 3 小时采样；边界日期可能仅有部分数据。
超出覆盖范围返回 forecast_date_unavailable 及 available_dates，不猜测天气。

`include_periods=false` 仅返回紧凑汇总，原始响应仍按需返回。
本地时间使用接口返回的固定 UTC 偏移，不是 IANA 时区或夏令时变化预测。原有实时天气工具保持兼容。

```json
{"location":"Nanjing,CN","forecast_date":"tomorrow","language":"zh"}
```

## 错误处理

失败统一为 `ok=false`、稳定的 `error` 和本地化 `message`：

| 错误码 | 含义 |
| --- | --- |
| `invalid_location`、`invalid_coordinates` | 输入无效，不发起请求 |
| `missing_api_key`、`invalid_config` | 未配置密钥或接口格式错误 |
| `timeout`、`request_failed` | 超时或网络连接失败 |
| `invalid_api_key`、`access_denied` | HTTP 401、403 |
| `location_not_found`、`rate_limited` | HTTP 404、429 |
| `provider_unavailable`、`openweather_error` | 服务端错误或其他失败状态 |
| `invalid_json`、`invalid_response` | JSON 无效、结构错误、必要字段缺失或数值无效 |
| `invalid_date`、`forecast_date_unavailable` | 日期参数无效，或请求日期没有预报采样 |

数字形式的 Retry-After 返回为 `retry_after_seconds`，最多一天。不自动重试，避免额外消耗配额。
不回显请求异常字符串和上游错误正文，避免含 appid 的 URL 泄漏密钥。禁止重定向，并及时关闭响应。
接口 URL 必须为不含账号密码、查询参数和片段的 HTTP(S) 地址；HTTP 供本地模拟使用，真实接口应使用 HTTPS。

## 配置

| 环境变量 | 默认值或作用 |
| --- | --- |
| `OPENWEATHER_API_KEY` | 服务商密钥；回退到 API_KEY_OPENWEATHER |
| `OPENWEATHER_BASE_URL` | https://api.openweathermap.org/data/2.5/weather；回退到 BASE_URL_OPENWEATHER |
| `OPENWEATHER_FORECAST_URL` | 标准接口为 https://api.openweathermap.org/data/2.5/forecast；未设置时将实时地址末尾 /weather 替换为 /forecast，自定义代理路径需要显式配置 |
| `WEATHER_MCP_HOST` | 0.0.0.0；仅本机使用时设为 127.0.0.1 |
| `WEATHER_MCP_PORT` | 16681；过大的无效值回退默认端口 |
| `WEATHER_MCP_TIMEOUT_SECONDS` | 10；Requests socket 超时，不是整个操作的总时限 |
| `WEATHER_MCP_ENABLE_DNS_REBINDING_PROTECTION` | false；启用 Host/Origin 校验 |
| `WEATHER_MCP_ALLOWED_HOSTS` | localhost 和 127.0.0.1 加配置端口，逗号分隔 |
| `WEATHER_MCP_ALLOWED_ORIGINS` | 空，逗号分隔 |
| `WEATHER_MCP_UVICORN_TIMEOUT_KEEP_ALIVE` | 1 秒 |
| `WEATHER_MCP_UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN` | 2 秒 |

保持可信教学环境默认，无内置鉴权及限流。共享部署应限制网络来源或使用鉴权网关；可连接的客户端能够消耗密钥配额。
支持当前天气和 5 天 / 3 小时预报，不提供历史天气。升级后重启服务并刷新工具列表。

## 在 Agent 中配置

服务是常驻 HTTP 进程，先启动服务，再在 Agent 里添加连接；密钥由服务进程读取，Agent 不需要持有。

### Codex

一行添加，`codex mcp list` 校验连接：

```bash
codex mcp add w-OpenWeatherMCP --url http://127.0.0.1:16681/mcp
codex mcp list
```

或直接写 `~/.codex/config.toml`，Codex 用 `url` 表示 Streamable HTTP：

```toml
[mcp_servers.w-OpenWeatherMCP]
url = "http://127.0.0.1:16681/mcp"
tool_timeout_sec = 30
```

远程机器上的服务把地址换成 `http://<服务器IP>:16681/mcp`，并按需开启 Host 校验。
改完配置需重启 Codex。课堂只读演示可加 `enabled_tools = ["get_weather", "get_forecast"]` 收敛工具面。

### Cherry Studio

界面路径：设置 → MCP 服务 → 添加服务器 → 类型选 **Streamable HTTP** →
服务器地址填 `http://127.0.0.1:16681/mcp` → 保存后打开开关，再在 Agent 中勾选该服务。

JSON 形式（Cherry Studio 的远程地址字段是 `baseUrl`，旧版本兼容 `url`）：

```json
{
  "mcpServers": {
    "w-OpenWeatherMCP": {
      "name": "w-OpenWeatherMCP",
      "description": "OpenWeather 实时天气与 5 天/3 小时预报",
      "type": "streamableHttp",
      "baseUrl": "http://127.0.0.1:16681/mcp",
      "isActive": true
    }
  }
}
```

工具与返回内容默认英文，需要中文时在调用里传 `language="zh"`，不是改客户端配置。

## 验证

在父级 `mcp` 目录执行：

```bash
python -B -m unittest -v test_forecast test_weather_demo test_bilingual_tools
```

覆盖中英文、异常数据、密钥脱敏、状态码及实际 HTTP MCP 调用；上游使用本地模拟，不消耗真实配额。
见 [Showcase](SHOWCASE.md)。
