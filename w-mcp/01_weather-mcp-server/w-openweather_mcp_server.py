"""OpenWeather current weather and 5-day/3-hour forecasts for trusted-network use.

Run: python w-openweather_mcp_server.py
Endpoint: http://<server-ip>:16681/mcp

Messages and weather descriptions default to English. Pass language="zh"
for Chinese. City names are sent unchanged to OpenWeather.
See README.md for configuration and provider coverage limits."""

import math
import os
import platform
from datetime import UTC, date, datetime, timedelta, timezone
from itertools import pairwise
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import requests
from dotenv import find_dotenv, load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

load_dotenv(find_dotenv())

DEFAULT_OPENWEATHER_BASE_URL = "https://api.openweathermap.org/data/2.5/weather"
DEFAULT_OPENWEATHER_FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"
DEFAULT_UVICORN_TIMEOUT_KEEP_ALIVE = 1
DEFAULT_UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN = 2


def _message(english: str, chinese: str, language: Literal["en", "zh"] = "en") -> str:
    """Localize server-authored text; never translate paths or file contents."""
    return chinese if language == "zh" else english


def _get_int_env(
    name: str, default: int, minimum: int = 1, maximum: int | None = None
) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    if maximum is not None and parsed > maximum:
        return default
    return max(minimum, parsed)


def _get_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _get_list_env(name: str) -> list[str]:
    value = os.getenv(name, "").strip()
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _transport_security_settings() -> TransportSecuritySettings:
    allowed_hosts = _get_list_env("WEATHER_MCP_ALLOWED_HOSTS")
    if not allowed_hosts:
        allowed_hosts = [
            f"127.0.0.1:{SERVER_PORT}",
            f"localhost:{SERVER_PORT}",
        ]

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=_get_bool_env(
            "WEATHER_MCP_ENABLE_DNS_REBINDING_PROTECTION"
        ),
        allowed_hosts=allowed_hosts,
        allowed_origins=_get_list_env("WEATHER_MCP_ALLOWED_ORIGINS"),
    )


REQUEST_TIMEOUT_SECONDS = _get_int_env("WEATHER_MCP_TIMEOUT_SECONDS", 10)
SERVER_PORT = _get_int_env("WEATHER_MCP_PORT", 16681, maximum=65535)
SERVER_HOST = os.getenv("WEATHER_MCP_HOST", "0.0.0.0").strip() or "0.0.0.0"

mcp = FastMCP(
    "w-OpenWeatherMCP",
    instructions=(
        "w-OpenWeatherMCP：实时天气与 5 天/3 小时预报（OpenWeather）。"
        "返回内容默认英文，传 language=\"zh\" 得到中文；"
        "地点名原样发给服务商，不翻译也不猜测。"
        "\n"
        "w-OpenWeatherMCP: current weather and 5-day/3-hour forecasts from "
        "OpenWeather. Responses default to English; pass language=\"zh\" for Chinese. "
        "Place names are forwarded to the provider unchanged, never translated or "
        "guessed."
    ),
    host=SERVER_HOST,
    port=SERVER_PORT,
    transport_security=_transport_security_settings(),
)


def _get_env(
    name: str, fallback_name: str | None = None, default: str | None = None
) -> str | None:
    value = os.getenv(name)
    if value:
        return value
    if fallback_name:
        value = os.getenv(fallback_name)
        if value:
            return value
    return default


def _openweather_config() -> tuple[str | None, str]:
    api_key = _get_env("OPENWEATHER_API_KEY", "API_KEY_OPENWEATHER")
    base_url = _get_env(
        "OPENWEATHER_BASE_URL",
        "BASE_URL_OPENWEATHER",
        DEFAULT_OPENWEATHER_BASE_URL,
    )
    return api_key.strip() if api_key else None, base_url.strip()


def _safe_base_url(base_url: str) -> str:
    """Diagnostics never echo credentials or query strings."""
    try:
        parts = urlsplit(base_url)
        return urlunsplit((parts.scheme, parts.hostname or "", parts.path, "", ""))
    except ValueError:
        return "invalid_url"


def _forecast_base_url(current_url: str) -> str:
    configured = os.getenv("OPENWEATHER_FORECAST_URL", "").strip()
    if configured:
        return configured
    if current_url == DEFAULT_OPENWEATHER_BASE_URL:
        return DEFAULT_OPENWEATHER_FORECAST_URL
    try:
        parts = urlsplit(current_url)
    except ValueError:
        return ""
    if parts.path.endswith("/weather"):
        return urlunsplit(parts._replace(path=parts.path[:-7] + "forecast"))
    return ""  # A custom proxy path needs an explicit forecast endpoint.


def _valid_base_url(base_url: str) -> bool:
    try:
        parts = urlsplit(base_url)
        return (
            parts.scheme in {"http", "https"}
            and bool(parts.hostname)
            and not parts.username
            and not parts.password
            and not parts.query
            and not parts.fragment
        )
    except ValueError:
        return False


def _format_weather(
    data: dict[str, Any], location: str | dict[str, float]
) -> dict[str, Any]:
    weather = data.get("weather") or [{}]
    main = data.get("main") or {}
    wind = data.get("wind") or {}
    sys_info = data.get("sys") or {}
    coord = data.get("coord") or {}
    clouds = data.get("clouds") or {}

    return {
        "ok": True,
        "query": location,
        "city": data.get("name"),
        "country": sys_info.get("country"),
        "latitude": coord.get("lat"),
        "longitude": coord.get("lon"),
        "temperature_celsius": main.get("temp"),
        "feels_like_celsius": main.get("feels_like"),
        "temperature_min_celsius": main.get("temp_min"),
        "temperature_max_celsius": main.get("temp_max"),
        "weather": weather[0].get("description"),
        "weather_main": weather[0].get("main"),
        "humidity_percent": main.get("humidity"),
        "pressure_hpa": main.get("pressure"),
        "wind_speed_mps": wind.get("speed"),
        "wind_direction_degrees": wind.get("deg"),
        "cloudiness_percent": clouds.get("all"),
        "visibility_meters": data.get("visibility"),
        "timezone_offset_seconds": data.get("timezone"),
        "updated_at_unix": data.get("dt"),
        "raw": data,
    }


@mcp.tool()
def ping() -> dict[str, Any]:
    """
    探活：检查天气 MCP 服务状态，不发起任何外部请求。

    Check the weather MCP server without making an external request.
    """
    _, base_url = _openweather_config()
    security_settings = _transport_security_settings()
    return {
        "ok": True,
        "server": "w-OpenWeatherMCP",
        "platform": platform.system(),
        "host": SERVER_HOST,
        "port": SERVER_PORT,
        "openweather_base_url": _safe_base_url(base_url),
        "openweather_forecast_url": _safe_base_url(_forecast_base_url(base_url)),
        "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
        "dns_rebinding_protection_enabled": security_settings.enable_dns_rebinding_protection,
        "allowed_hosts": security_settings.allowed_hosts,
        "allowed_origins": security_settings.allowed_origins,
    }


@mcp.tool()
def check_config(language: Literal["en", "zh"] = "en") -> dict[str, Any]:
    """
    检查 OpenWeather 配置是否就绪，不发起外部请求。
    language 取 en（默认）或 zh。

    Check OpenWeather configuration without making an external request. language: en
    (default) or zh.
    """
    api_key, base_url = _openweather_config()
    result = {
        "ok": bool(api_key) and _valid_base_url(base_url),
        "has_api_key": bool(api_key),
        "base_url": _safe_base_url(base_url),
        "forecast_url": _safe_base_url(_forecast_base_url(base_url)),
        "forecast_configured": _valid_base_url(_forecast_base_url(base_url)),
        "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
        "message": _message(
            "OpenWeather configuration loaded.",
            "OpenWeather 配置已加载。",
            language,
        )
        if api_key and _valid_base_url(base_url)
        else _message(
            "Missing OPENWEATHER_API_KEY or API_KEY_OPENWEATHER, or invalid base URL.",
            "缺少 OPENWEATHER_API_KEY 或 API_KEY_OPENWEATHER，或 API 地址无效。",
            language,
        ),
    }
    if not result["ok"]:
        result["error"] = "missing_api_key" if not api_key else "invalid_config"
    return result


def _error(code, english, chinese, language, **details):
    return {
        "ok": False,
        "error": code,
        "message": _message(english, chinese, language),
        **details,
    }


def _provider_error(status, language, retry_after=None):
    code, english, chinese = {
        401: (
            "invalid_api_key",
            "OpenWeather rejected the API key.",
            "OpenWeather 拒绝了 API key。",
        ),
        403: (
            "access_denied",
            "OpenWeather denied access to this endpoint.",
            "OpenWeather 拒绝访问此接口。",
        ),
        404: (
            "location_not_found",
            "The requested location was not found.",
            "未找到请求的地点。",
        ),
        429: (
            "rate_limited",
            "OpenWeather rate limit reached; retry later.",
            "OpenWeather 请求超限，请稍后重试。",
        ),
    }.get(
        status,
        (
            "provider_unavailable",
            "OpenWeather is temporarily unavailable.",
            "OpenWeather 暂时不可用。",
        )
        if status >= 500
        else (
            "openweather_error",
            "OpenWeather rejected the request.",
            "OpenWeather 拒绝了请求。",
        ),
    )
    result = _error(code, english, chinese, language, status_code=status)
    if isinstance(retry_after, str) and retry_after.isdigit():
        result["retry_after_seconds"] = min(int(retry_after), 86400)
    return result


def _redact(value, secret):
    if isinstance(value, str):
        return value.replace(secret, "[redacted]")
    if isinstance(value, list):
        return [_redact(item, secret) for item in value]
    if isinstance(value, dict):
        return {
            _redact(key, secret): _redact(item, secret) for key, item in value.items()
        }
    return value


def _finite_number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _utc_now():
    return datetime.now(UTC)


def _valid_forecast_date(value):
    if value in {"today", "tomorrow", "all"}:
        return True
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _format_forecast(data, query, requested, include_periods, include_raw, language):
    def invalid():
        return _error(
            "invalid_response",
            "OpenWeather returned invalid forecast fields.",
            "OpenWeather 返回了无效的预报字段。",
            language,
        )

    city, entries = data.get("city"), data.get("list")
    if (
        not isinstance(city, dict)
        or not isinstance(entries, list)
        or not 1 <= len(entries) <= 40
    ):
        return invalid()
    offset = city.get("timezone")
    coordinates = city.get("coord")
    if (
        type(offset) is not int
        or not -86399 <= offset <= 86399
        or not isinstance(coordinates, dict)
        or not _finite_number(coordinates.get("lat"))
        or not -90 <= coordinates["lat"] <= 90
        or not _finite_number(coordinates.get("lon"))
        or not -180 <= coordinates["lon"] <= 180
    ):
        return invalid()
    zone = timezone(timedelta(seconds=offset))
    now = _utc_now()
    local_today = now.astimezone(zone).date()
    target = (
        local_today
        if requested == "today"
        else local_today + timedelta(days=1)
        if requested == "tomorrow"
        else None
        if requested == "all"
        else date.fromisoformat(requested)
    )
    periods, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            return invalid()
        timestamp = entry.get("dt")
        main, conditions, wind = (
            entry.get("main"),
            entry.get("weather"),
            entry.get("wind", {}),
        )
        if (
            type(timestamp) is not int
            or timestamp in seen
            or not isinstance(main, dict)
            or not _finite_number(main.get("temp"))
            or not isinstance(conditions, list)
            or not conditions
            or not isinstance(conditions[0], dict)
            or not isinstance(conditions[0].get("description"), str)
            or not isinstance(wind, dict)
        ):
            return invalid()
        seen.add(timestamp)
        try:
            instant = datetime.fromtimestamp(timestamp, UTC)
            local = instant.astimezone(zone)
        except (OSError, OverflowError, ValueError):
            return invalid()
        numeric = {
            "feels_like_celsius": main.get("feels_like"),
            "humidity_percent": main.get("humidity"),
            "wind_speed_mps": wind.get("speed"),
            "wind_direction_degrees": wind.get("deg"),
            "precipitation_probability": entry.get("pop"),
        }
        if any(
            value is not None and not _finite_number(value)
            for value in numeric.values()
        ):
            return invalid()
        probability = numeric["precipitation_probability"]
        if probability is not None and not 0 <= probability <= 1:
            return invalid()
        for key in ["rain", "snow"]:
            precipitation = entry.get(key, {})
            if not isinstance(precipitation, dict):
                return invalid()
            amount = precipitation.get("3h")
            if amount is not None and (not _finite_number(amount) or amount < 0):
                return invalid()
            numeric[key + "_3h_mm"] = amount
        periods.append(
            {
                "timestamp_unix": timestamp,
                "time_utc": instant.isoformat(),
                "time_local": local.isoformat(),
                "date_local": local.date().isoformat(),
                "temperature_celsius": main["temp"],
                "weather": conditions[0]["description"],
                **numeric,
            }
        )
    periods.sort(key=lambda item: item["timestamp_unix"])
    available = sorted({item["date_local"] for item in periods})
    selected = [
        item
        for item in periods
        if target is None or item["date_local"] == target.isoformat()
    ]
    if not selected:
        return _error(
            "forecast_date_unavailable",
            "The requested local date is not covered by this forecast.",
            "此预报未覆盖请求的地点当地日期。",
            language,
            requested_date=target.isoformat(),
            available_dates=available,
            timezone_offset_seconds=offset,
        )
    summaries = []
    for day in sorted({item["date_local"] for item in selected}):
        samples = [item for item in selected if item["date_local"] == day]
        probabilities = [
            item["precipitation_probability"]
            for item in samples
            if item["precipitation_probability"] is not None
        ]
        speeds = [
            item["wind_speed_mps"]
            for item in samples
            if item["wind_speed_mps"] is not None
        ]
        summaries.append(
            {
                "date_local": day,
                "period_count": len(samples),
                "has_eight_3h_samples": len(samples) == 8
                and all(
                    b["timestamp_unix"] - a["timestamp_unix"] == 10800
                    for a, b in pairwise(samples)
                ),
                "sampled_temperature_min_celsius": min(
                    item["temperature_celsius"] for item in samples
                ),
                "sampled_temperature_max_celsius": max(
                    item["temperature_celsius"] for item in samples
                ),
                "weather_descriptions": list(
                    dict.fromkeys(item["weather"] for item in samples)
                ),
                "max_period_precipitation_probability": max(probabilities)
                if probabilities
                else None,
                "max_sampled_wind_speed_mps": max(speeds) if speeds else None,
                "first_period_local": samples[0]["time_local"],
                "last_period_local": samples[-1]["time_local"],
            }
        )
    result = {
        "ok": True,
        "query": query,
        "city": city.get("name"),
        "country": city.get("country"),
        "latitude": coordinates["lat"],
        "longitude": coordinates["lon"],
        "forecast_interval_hours": 3,
        "timezone_offset_seconds": offset,
        "reference_time_utc": now.isoformat(),
        "location_today": local_today.isoformat(),
        "requested_date": target.isoformat() if target else None,
        "available_dates": available,
        "period_count": len(selected),
        "summaries": summaries,
        "summary_note": _message(
            "Summaries use returned 3-hour samples, not official daily extrema or daily rain probability. Times use the provider's fixed UTC offset.",
            "汇总基于返回的 3 小时采样，不是官方全天极值或全天降水概率；时间使用服务商返回的固定 UTC 偏移。",
            language,
        ),
    }
    if include_periods:
        result["periods"] = selected
    if include_raw:
        result["raw"] = data
    return result


def _request_weather(
    query,
    params,
    language,
    include_raw,
    forecast=False,
    forecast_date="tomorrow",
    include_periods=True,
):
    api_key, base_url = _openweather_config()
    if forecast:
        base_url = _forecast_base_url(base_url)
    if not api_key:
        return _error(
            "missing_api_key",
            "Set OPENWEATHER_API_KEY or API_KEY_OPENWEATHER in .env.",
            "请在 .env 中配置 OPENWEATHER_API_KEY 或 API_KEY_OPENWEATHER。",
            language,
        )
    if not _valid_base_url(base_url):
        return _error(
            "invalid_config",
            "Configure an HTTP(S) endpoint without credentials, query or fragment.",
            "请配置不含账号密码、查询参数或片段的 HTTP(S) 接口。",
            language,
        )
    try:
        response = requests.get(
            base_url,
            params={
                **params,
                "appid": api_key,
                "units": "metric",
                "lang": "zh_cn" if language == "zh" else "en",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        response.raise_for_status()
    except requests.Timeout:
        return _error(
            "timeout",
            f"The OpenWeather request timed out after {REQUEST_TIMEOUT_SECONDS} seconds.",
            f"请求 OpenWeather 超时，超过 {REQUEST_TIMEOUT_SECONDS} 秒未响应。",
            language,
        )
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        retry_after = (
            exc.response.headers.get("Retry-After")
            if exc.response is not None
            else None
        )
        if exc.response is not None:
            exc.response.close()
        return (
            _provider_error(status, language, retry_after)
            if isinstance(status, int)
            else _error(
                "request_failed",
                "The weather HTTP request failed.",
                "天气 HTTP 请求失败。",
                language,
            )
        )
    except requests.RequestException:
        # Request exception strings may contain the full URL, including appid.
        return _error(
            "request_failed",
            "Could not connect to OpenWeather.",
            "无法连接 OpenWeather。",
            language,
        )
    try:
        status = response.status_code
        if isinstance(status, int) and status != 200:
            return _provider_error(
                status, language, response.headers.get("Retry-After")
            )
        try:
            data = response.json()
        except ValueError:
            return _error(
                "invalid_json",
                "OpenWeather returned invalid JSON.",
                "OpenWeather 返回内容不是合法 JSON。",
                language,
            )
        if not isinstance(data, dict):
            return _error(
                "invalid_response",
                "OpenWeather returned an unexpected response structure.",
                "OpenWeather 返回了非预期的数据结构。",
                language,
            )
        if str(data.get("cod")) != "200":
            try:
                status = int(data.get("cod", 0))
            except (TypeError, ValueError):
                status = 0
            return _provider_error(status, language)
        if forecast:
            return _redact(
                _format_forecast(
                    data, query, forecast_date, include_periods, include_raw, language
                ),
                api_key,
            )
        main, weather = data.get("main"), data.get("weather")
        if (
            not isinstance(main, dict)
            or not _finite_number(main.get("temp"))
            or not isinstance(weather, list)
            or not weather
            or not isinstance(weather[0], dict)
            or not isinstance(weather[0].get("description"), str)
            or any(
                data.get(key) is not None and not isinstance(data[key], dict)
                for key in ["wind", "sys", "coord", "clouds"]
            )
        ):
            return _error(
                "invalid_response",
                "OpenWeather returned missing or invalid weather fields.",
                "OpenWeather 返回的天气字段缺失或无效。",
                language,
            )
        result = _format_weather(data, query)
        for key, value in result.items():
            if (
                key
                not in {
                    "ok",
                    "raw",
                    "query",
                    "city",
                    "country",
                    "weather",
                    "weather_main",
                }
                and value is not None
                and not _finite_number(value)
            ):
                return _error(
                    "invalid_response",
                    "OpenWeather returned invalid numeric fields.",
                    "OpenWeather 返回了无效的数值字段。",
                    language,
                )
        if not include_raw:
            result.pop("raw", None)
        # Never echo the configured secret even if an upstream proxy reflects it.
        return _redact(result, api_key)
    finally:
        response.close()


@mcp.tool()
def get_weather(
    location: str, language: Literal["en", "zh"] = "en", include_raw: bool = False
) -> dict[str, Any]:
    """
    按城市查询实时天气（公制单位）。
    重名城市用 city,country 形式区分，例如 Suzhou,CN。
    language 取 en（默认）或 zh；
    服务商原始数据需显式开启。

    Get current metric weather by city. Use city,country to disambiguate. language: en
    (default) or zh. Raw provider data is opt-in.
    """
    location = location.strip()
    if not location or len(location) > 200:
        return _error(
            "invalid_location",
            "Provide a city name, for example London or Beijing.",
            "请传入城市名称，例如 Beijing。",
            language,
        )
    return _request_weather(location, {"q": location}, language, include_raw)


@mcp.tool()
def get_weather_by_coordinates(
    latitude: float,
    longitude: float,
    language: Literal["en", "zh"] = "en",
    include_raw: bool = False,
) -> dict[str, Any]:
    """
    按纬度 [-90,90] 与经度 [-180,180] 查询实时天气（公制单位）。
    language 取 en（默认）或 zh；
    坐标查询可避开城市重名歧义。

    Get current metric weather by latitude [-90,90] and longitude [-180,180]. language: en
    (default) or zh; coordinates avoid city-name ambiguity.
    """
    if (
        not _finite_number(latitude)
        or not _finite_number(longitude)
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        return _error(
            "invalid_coordinates",
            "Latitude must be -90..90 and longitude -180..180.",
            "纬度必须为 -90..90，经度必须为 -180..180。",
            language,
        )
    return _request_weather(
        {"latitude": latitude, "longitude": longitude},
        {"lat": latitude, "lon": longitude},
        language,
        include_raw,
    )


@mcp.tool()
def get_forecast(
    location: str,
    forecast_date: str = "tomorrow",
    language: Literal["en", "zh"] = "en",
    include_periods: bool = True,
    include_raw: bool = False,
) -> dict[str, Any]:
    """
    按城市查询 OpenWeather 5 天/3 小时预报。
    forecast_date 取 tomorrow（默认）、today、all 或 YYYY-MM-DD；
    日期按该地点的服务商 UTC 偏移计算，不使用服务器时区。
    汇总是采样值，不是官方日最高/最低气温。
    language 取 en（默认）或 zh；
    原始数据需显式开启。

    OpenWeather 5-day/3-hour forecast by city. forecast_date: tomorrow (default), today,
    all, or YYYY-MM-DD. Dates use the location's provider UTC offset, not the server
    timezone. Summaries are sampled values, not official daily extrema. language: en
    (default) or zh. Raw data is opt-in.
    """
    location = location.strip()
    if not location or len(location) > 200:
        return _error(
            "invalid_location",
            "Provide a city name, for example Nanjing,CN.",
            "请提供城市名，例如 Nanjing,CN。",
            language,
        )
    if not _valid_forecast_date(forecast_date):
        return _error(
            "invalid_date",
            "Use tomorrow, today, all, or YYYY-MM-DD.",
            "请使用 tomorrow、today、all 或 YYYY-MM-DD。",
            language,
        )
    return _request_weather(
        location,
        {"q": location},
        language,
        include_raw,
        True,
        forecast_date,
        include_periods,
    )


@mcp.tool()
def get_forecast_by_coordinates(
    latitude: float,
    longitude: float,
    forecast_date: str = "tomorrow",
    language: Literal["en", "zh"] = "en",
    include_periods: bool = True,
    include_raw: bool = False,
) -> dict[str, Any]:
    """
    按坐标查询 OpenWeather 5 天/3 小时预报。
    forecast_date 取 tomorrow、today、all 或 YYYY-MM-DD；
    日期按该地点的服务商 UTC 偏移计算。
    language 取 en（默认）或 zh；
    原始数据需显式开启。

    OpenWeather 5-day/3-hour forecast by coordinates. forecast_date: tomorrow, today, all or
    YYYY-MM-DD; dates use the provider's location UTC offset. language: en (default) or zh;
    raw data is opt-in.
    """
    if (
        not _finite_number(latitude)
        or not _finite_number(longitude)
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        return _error(
            "invalid_coordinates",
            "Latitude must be -90..90 and longitude -180..180.",
            "纬度必须为 -90..90，经度必须为 -180..180。",
            language,
        )
    if not _valid_forecast_date(forecast_date):
        return _error(
            "invalid_date",
            "Use tomorrow, today, all, or YYYY-MM-DD.",
            "请使用 tomorrow、today、all 或 YYYY-MM-DD。",
            language,
        )
    return _request_weather(
        {"latitude": latitude, "longitude": longitude},
        {"lat": latitude, "lon": longitude},
        language,
        include_raw,
        True,
        forecast_date,
        include_periods,
    )


async def _run_streamable_http_async() -> None:
    """Run Streamable HTTP with short shutdown-related uvicorn timeouts."""
    import uvicorn

    config = uvicorn.Config(
        mcp.streamable_http_app(),
        host=mcp.settings.host,
        port=mcp.settings.port,
        log_level=mcp.settings.log_level.lower(),
        timeout_keep_alive=_get_int_env(
            "WEATHER_MCP_UVICORN_TIMEOUT_KEEP_ALIVE",
            DEFAULT_UVICORN_TIMEOUT_KEEP_ALIVE,
            minimum=0,
        ),
        timeout_graceful_shutdown=_get_int_env(
            "WEATHER_MCP_UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN",
            DEFAULT_UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN,
            minimum=0,
        ),
    )
    server = uvicorn.Server(config)
    await server.serve()


def run_server() -> None:
    """Start the MCP server."""
    import anyio

    try:
        anyio.run(_run_streamable_http_async)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run_server()


# Example Cline / MCP client configuration:
#
# {
#   "mcpServers": {
#     "w-OpenWeatherMCP": {
#       "name": "w-OpenWeatherMCP",
#       "description": "Current weather via OpenWeather; English by default, with optional Chinese output",
#       "url": "http://<server-ip>:16681/mcp",
#       "type": "streamableHttp",
#       "isActive": true
#     }
#   }
# }
