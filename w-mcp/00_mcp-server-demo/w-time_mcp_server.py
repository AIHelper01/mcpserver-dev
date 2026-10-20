"""Timezone-aware time tools over stdio. Run: python w-time_mcp_server.py."""

from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "w-TimeMCP",
    instructions=(
        "w-TimeMCP：时区感知的时间工具，基于 stdio 传输。"
        "需要系统时区数据或 tzdata 才能使用 IANA 时区。"
        "\n"
        "w-TimeMCP: timezone-aware time tools over stdio. IANA zones require system "
        "timezone data or tzdata."
    )
)


def _error(code: str, english: str, chinese: str, language: str):
    return {
        "ok": False,
        "error": code,
        "message": chinese if language == "zh" else english,
    }


def _zone(name: str):
    return UTC if name.upper() == "UTC" else ZoneInfo(name)


@mcp.tool()
def get_current_time(
    timezone_name: str = "", language: Literal["en", "zh"] = "en"
) -> dict:
    """
    返回带时区的 ISO 时间和 Unix 时间戳。
    timezone_name 留空则使用服务器本地时区；
    UTC 始终可用；
    IANA 时区需要系统时区数据或 tzdata。

    Return aware ISO time and Unix timestamp. Empty timezone_name uses server local time;
    UTC is always available; IANA zones need system timezone data or tzdata.
    """
    try:
        now = (
            datetime.now(_zone(timezone_name))
            if timezone_name
            else datetime.now().astimezone()
        )
    except (ZoneInfoNotFoundError, ValueError):
        return _error(
            "invalid_timezone",
            "Unknown timezone or missing timezone data; install tzdata if needed.",
            "时区无效或缺少时区数据，必要时请安装 tzdata。",
            language,
        )
    return {
        "ok": True,
        "current_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "iso8601": now.isoformat(timespec="seconds"),
        "unix_timestamp": now.timestamp(),
        "timezone": timezone_name or str(now.tzinfo),
        "utc_offset_seconds": int(now.utcoffset().total_seconds()),
    }


@mcp.tool()
def convert_time(
    iso_time: str,
    to_timezone: str = "UTC",
    from_timezone: str = "",
    language: Literal["en", "zh"] = "en",
) -> dict:
    """
    把 ISO-8601 时间换算到指定 IANA 时区或 UTC。
    不含时区信息的时间必须提供 from_timezone。
    夏令时歧义或不存在的本地时间会被拒绝，请给出明确 UTC 偏移。

    Convert ISO-8601 time to an IANA zone or UTC. Naive times require from_timezone.
    Ambiguous/nonexistent DST local times are rejected; supply an explicit offset.
    """
    try:
        target = _zone(to_timezone)
        source_zone = _zone(from_timezone) if from_timezone else None
    except (ZoneInfoNotFoundError, ValueError):
        return _error(
            "invalid_timezone",
            "Unknown timezone or missing timezone data; install tzdata if needed.",
            "时区无效或缺少时区数据，必要时请安装 tzdata。",
            language,
        )
    try:
        if ":" not in iso_time or len(iso_time) > 100:
            raise ValueError("A date and time are required")
        source = datetime.fromisoformat(iso_time)
    except ValueError:
        return _error(
            "invalid_time",
            "Provide a valid ISO-8601 date and time.",
            "请提供有效的 ISO-8601 日期时间。",
            language,
        )
    if source.tzinfo is None:
        if source_zone is None:
            return _error(
                "timezone_required",
                "A naive time requires from_timezone or an explicit UTC offset.",
                "无偏移的时间需要 from_timezone 或显式 UTC 偏移。",
                language,
            )
        source = source.replace(tzinfo=source_zone)
        try:
            round_trip = (
                source.astimezone(UTC).astimezone(source_zone).replace(tzinfo=None)
            )
        except (OverflowError, OSError, ValueError):
            return _error(
                "invalid_time",
                "The date is outside the supported conversion range.",
                "日期超出支持的转换范围。",
                language,
            )
        if round_trip != source.replace(tzinfo=None):
            return _error(
                "nonexistent_time",
                "This local time does not exist due to a clock change.",
                "时钟调整导致此本地时间不存在。",
                language,
            )
        if source.utcoffset() != source.replace(fold=1).utcoffset():
            return _error(
                "ambiguous_time",
                "This local time is ambiguous; supply an explicit UTC offset.",
                "此本地时间有歧义，请提供显式 UTC 偏移。",
                language,
            )
    elif source_zone is not None:
        return _error(
            "conflicting_timezone",
            "Do not combine an explicit offset with from_timezone.",
            "显式 UTC 偏移不能与 from_timezone 同时使用。",
            language,
        )
    try:
        converted = source.astimezone(target)
        timestamp = source.timestamp()
    except (OverflowError, OSError, ValueError):
        return _error(
            "invalid_time",
            "The date is outside the supported conversion range.",
            "日期超出支持的转换范围。",
            language,
        )
    return {
        "ok": True,
        "source_iso8601": source.isoformat(),
        "target_iso8601": converted.isoformat(),
        "timezone": to_timezone,
        "unix_timestamp": timestamp,
    }


def run_server():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_server()
