[English](../../../english/02_mcp/cases/01_weather-mcp-server/SHOWCASE.md) | 简体中文

# w-OpenWeather MCP：中英文天气查询

本文区分可复现的模拟测试与标注日期的真实验证。自动测试使用本地模拟天气接口及真实 HTTP MCP 客户端/服务，不消耗真实密钥或配额；末节另记升级预报后的真实调用结果。

## 演示流程

1. 调用 `ping`、`check_config` 检查服务及配置。
2. `get_weather(location="London,GB")` 默认返回英文。
3. 增加 `language="zh"`，验证中文天气描述与错误提示。
4. `get_weather_by_coordinates(latitude=39.9, longitude=116.4)` 通过坐标避免城市名称歧义。
5. 仅在诊断时指定 `include_raw=true`。

```json
{"latitude":39.9,"longitude":116.4,"language":"zh","include_raw":false}
```

字段名保持英文，温度为摄氏度，风速为米每秒，地点不翻译。模拟天气描述是固定测试数据，不代表现实中的当前天气。

## 故障演示

| 场景 | 预期 |
| --- | --- |
| 空或过长城市名、越界或非有限坐标 | 本地化校验错误，不发请求 |
| 缺少密钥、接口格式无效 | 本地化配置错误，不请求天气 |
| HTTP 401、403、404、429 | invalid_api_key、access_denied、location_not_found、rate_limited |
| 异常字符串包含密钥 URL | 返回本地化错误，不回显 URL 和密钥 |
| HTTP 200 但 JSON/结构/必要字段错误、出现 NaN | invalid_json 或 invalid_response |
| 原始数据反射密钥 | 配置的密钥被脱敏 |

以上状态均覆盖 en/zh，错误码和 JSON 字段稳定；HTTP 集成还覆盖两种语言的城市、坐标及地点不存在场景。

## 改进与边界

统一单个 w- 入口，增加坐标查询、密钥脱敏、响应校验、按需原始数据及响应关闭。
不新增自动重试、自动验证真实密钥或缓存；socket 超时不是整个操作总时限。
真实城市覆盖和密钥权限取决于服务商，坐标参数依据 [官方接口](https://openweathermap.org/api/current)。

父级 mcp 目录执行：

```bash
python -B -m unittest -v test_weather_demo test_bilingual_tools
python -B -m unittest -v test_forecast
```

本次验证环境为 Windows/Python 3.12，未在 Linux/macOS 实测。

## 预报流程与真实验证

新增 get_forecast 和 get_forecast_by_coordinates，使用 [OpenWeather 预报接口](https://openweathermap.org/forecast5)，沿用当前密钥。

```text
get_forecast(location="Nanjing,CN", forecast_date="tomorrow", language="zh", include_periods=false)
get_forecast_by_coordinates(latitude=32.06, longitude=118.78, forecast_date="tomorrow", language="en")
```

日期支持 today、tomorrow、all 或 ISO 日期，相对日期按地点 UTC 偏移计算。
汇总来自 3 小时采样，不是官方全天温度极值或全天降水概率；未覆盖日期明确报错并给出可选日期，原始数据仍按需返回。

2026-10-17 约 15:27 UTC，通过后台 MCP 服务及真实密钥完成中英文验证。
南京 10 月 18 日返回八个采样，采样温度 17.04..23.64°C，小雨/中雨，最高分时降水概率 1.0，最大采样风速 4.92m/s。
这是标注时间的验证记录，不应在之后直接复用为实时预报。升级后的后台服务已暴露六个工具。
