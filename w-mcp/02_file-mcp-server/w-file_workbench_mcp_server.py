"""w-File Workbench MCP server for classroom and trusted-network use.

Run: python w-file_workbench_mcp_server.py
Endpoint: http://<server-ip>:16682/mcp

Tool messages default to English; pass language="zh" for Chinese.
Paths and file contents are preserved in their original language.
There is no root-directory allowlist; OS account permissions determine access.
See README.md for environment variables and deployment limits."""

from __future__ import annotations

import codecs
import fnmatch
import hashlib
import inspect
import json
import os
import platform
import re
import shutil
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Literal

from dotenv import find_dotenv, load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

load_dotenv(find_dotenv())

SERVER_DIR = Path(__file__).resolve().parent
STATE_DIR = (
    Path(os.getenv("FILE_MCP_STATE_DIR", str(SERVER_DIR))).expanduser().resolve()
)
HISTORY_FILE = STATE_DIR / "secure_rename_history.json"
DELETED_FILES_DIR = STATE_DIR / ".deleted_files"
SNAPSHOTS_DIR = STATE_DIR / ".snapshots"
_THREAD_LOCK = threading.RLock()
_LOCK_DEPTH = threading.local()
DEFAULT_MAX_READ_BYTES = 200_000
DEFAULT_UVICORN_TIMEOUT_KEEP_ALIVE = 1
DEFAULT_UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN = 2
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}
WINDOWS_INVALID_CHARS = set('<>:"/\\|?*')
WINDOWS_DRIVE_PATH_PATTERN = re.compile(r"^[a-zA-Z]:[\\/]")
WINDOWS_UNC_PATH_PATTERN = re.compile(r"^(?:\\\\|//)[^\\/]+[\\/][^\\/]+")


def _message(english: str, chinese: str, language: Literal["en", "zh"] = "en") -> str:
    """Localize server-authored text; never translate paths or file contents."""
    return chinese if language == "zh" else english


class FileOperationError(Exception):
    def __init__(self, code: str, english: str, chinese: str, **details):
        super().__init__(english)
        self.code, self.english, self.chinese, self.details = (
            code,
            english,
            chinese,
            details,
        )


@contextmanager
def _operation_lock():
    # ponytail: one lock serializes this teaching server; use per-root locks if needed.
    with _THREAD_LOCK:
        if getattr(_LOCK_DEPTH, "active", False):
            yield
            return
        lock_path = HISTORY_FILE.with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + 10
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise FileOperationError(
                            "server_busy",
                            "Server is busy; retry later.",
                            "服务繁忙，请稍后重试。",
                        )
                    time.sleep(0.05)
            _LOCK_DEPTH.active = True
            try:
                yield
            finally:
                _LOCK_DEPTH.active = False
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)


def _tool_guard(function):
    signature = inspect.signature(function)

    @wraps(function)
    def guarded(*args, **kwargs):
        language = signature.bind(*args, **kwargs).arguments.get("language", "en")
        try:
            with _operation_lock():
                return function(*args, **kwargs)
        except FileOperationError as exc:
            return {
                "ok": False,
                "error": exc.code,
                "message": _message(exc.english, exc.chinese, language),
                **exc.details,
            }
        except (
            OSError,
            ValueError,
            UnicodeError,
            RuntimeError,
            KeyError,
            TypeError,
        ) as exc:
            code = (
                "permission_denied"
                if isinstance(exc, PermissionError)
                else "operation_failed"
            )
            return {
                "ok": False,
                "error": code,
                "message": _message(
                    "The operation failed; inspect detail before retrying.",
                    "操作失败，请查看 detail 后再重试。",
                    language,
                ),
                "detail": str(exc),
            }

    return guarded


def _fingerprint(path: Path) -> dict[str, Any]:
    before = path.stat()
    if path.is_symlink() or not path.is_file():
        raise FileOperationError(
            "not_a_file", "A regular file is required.", "目标必须是普通文件。"
        )
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise FileOperationError(
            "file_changed", "File changed during inspection.", "检查期间文件发生变化。"
        )
    return {
        "device": after.st_dev,
        "inode": after.st_ino,
        "size": after.st_size,
        "mtime_ns": after.st_mtime_ns,
        "sha256": digest.hexdigest(),
    }


def _same_file(path: Path, fingerprint: dict) -> bool:
    return (
        path.is_file() and not path.is_symlink() and _fingerprint(path) == fingerprint
    )


def _publish(temp: Path, target: Path, overwrite: bool = False):
    if overwrite:
        os.replace(temp, target)
    else:
        # Hard-link publication is atomic and refuses existing targets on both OSes.
        os.link(temp, target)
        temp.unlink()


def _atomic_bytes(path: Path, payload: bytes, overwrite: bool = False):
    fd, name = tempfile.mkstemp(prefix=".file-mcp-", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite and path.is_file():
            shutil.copymode(path, temp)
        _publish(temp, path, overwrite)
    finally:
        temp.unlink(missing_ok=True)


def _atomic_json(path: Path, data):
    _atomic_bytes(
        path,
        json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"),
        overwrite=True,
    )


def _move_no_replace(source: Path, target: Path):
    if os.name == "nt":
        source.rename(target)
    else:
        os.link(source, target, follow_symlinks=False)
        try:
            source.unlink()
        except OSError:
            target.unlink()
            raise


def _record_dir(root: Path, record_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", record_id):
        raise FileOperationError(
            "invalid_record_id", "Invalid record ID.", "记录 ID 无效。"
        )
    path = root / record_id
    if path.is_symlink():
        raise FileOperationError(
            "invalid_record",
            "Backup symlinks are not supported.",
            "不支持备份符号链接。",
        )
    return path


def _check_backup_capacity(additional: int):
    maximum = _env_int("FILE_MCP_MAX_BACKUP_BYTES", 1_073_741_824, min_value=1)
    used = sum(
        p.stat().st_size
        for root in [DELETED_FILES_DIR, SNAPSHOTS_DIR]
        if root.exists()
        for p in root.rglob("*")
        if p.is_file()
    )
    if used + additional > maximum:
        raise FileOperationError(
            "backup_limit",
            "Backup storage limit exceeded; preview cleanup first.",
            "备份容量超限，请先预览清理。",
        )


def _copy_verified(source: Path, target: Path, sha256: str):
    fd, name = tempfile.mkstemp(prefix=".file-mcp-", dir=target.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as stream:
            shutil.copyfileobj(stream, output)
            output.flush()
            os.fsync(output.fileno())
        if _fingerprint(temp)["sha256"] != sha256:
            raise FileOperationError(
                "backup_corrupt", "Backup checksum mismatch.", "备份校验和不匹配。"
            )
        shutil.copystat(source, temp)
        _publish(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def _env_bool(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name, "").strip().lower()
    if not raw_value:
        return default
    return raw_value in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, min_value: int = 0) -> int:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return max(min_value, value)


def _env_list(name: str) -> list[str]:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return []
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _configured_transport_security() -> TransportSecuritySettings:
    port = _env_int("FILE_MCP_PORT", 16682, min_value=1)
    allowed_hosts = _env_list("FILE_MCP_ALLOWED_HOSTS")
    if not allowed_hosts:
        allowed_hosts = [
            f"127.0.0.1:{port}",
            f"localhost:{port}",
        ]

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=_env_bool(
            "FILE_MCP_ENABLE_DNS_REBINDING_PROTECTION"
        ),
        allowed_hosts=allowed_hosts,
        allowed_origins=_env_list("FILE_MCP_ALLOWED_ORIGINS"),
    )


mcp = FastMCP(
    "w-FileWorkbenchMCP",
    instructions=(
        "w-FileWorkbenchMCP：面向课堂演示与可信内网的本机文件工作台。"
        "可访问范围由服务账号的操作系统权限决定；"
        "删除、重命名和快照都保留备份，写入类工具请在可信环境中使用。"
        "\n"
        "w-FileWorkbenchMCP: local file workbench for classroom demos and trusted "
        "networks. Reachable paths follow the service account's OS permissions; "
        "deletions, renames and snapshots keep backups, so run write tools only in "
        "trusted environments."
    ),
    host=os.getenv("FILE_MCP_HOST", "0.0.0.0").strip() or "0.0.0.0",
    port=_env_int("FILE_MCP_PORT", 16682, min_value=1),
    transport_security=_configured_transport_security(),
)


def _max_read_bytes() -> int:
    return _env_int("FILE_MCP_MAX_READ_BYTES", DEFAULT_MAX_READ_BYTES, min_value=1)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _looks_like_windows_absolute_path(path: str) -> bool:
    stripped = path.strip()
    return bool(
        WINDOWS_DRIVE_PATH_PATTERN.match(stripped)
        or WINDOWS_UNC_PATH_PATTERN.match(stripped)
    )


def _resolve_path(
    path: str = ".", base_dir: str = "", language: Literal["en", "zh"] = "en"
) -> tuple[Path | None, str | None]:
    raw_path = str(path) or "."
    raw_base_dir = str(base_dir)

    if platform.system() != "Windows":
        for raw_value in [raw_base_dir, raw_path]:
            if _looks_like_windows_absolute_path(raw_value):
                return None, (
                    _message(
                        "A Windows absolute path was supplied, but this MCP server runs on "
                        f"{platform.system()} and cannot access that Windows directory directly: {raw_value}. "
                        "Run this server on Windows, or mount the directory and use its mounted path.",
                        "检测到 Windows 绝对路径，但当前 MCP 服务运行在 "
                        f"{platform.system()}，无法直接访问该 Windows 本地目录: {raw_value}。"
                        "请在 Windows 机器上启动本 MCP 服务，或把目录挂载到当前系统后使用挂载路径。",
                        language,
                    )
                )

    candidate = Path(raw_path).expanduser()
    if raw_base_dir:
        base_candidate = Path(raw_base_dir).expanduser()
        candidate = base_candidate / candidate
    elif not candidate.is_absolute():
        candidate = Path.cwd() / candidate

    try:
        resolved = candidate.resolve()
    except OSError as exc:
        return None, _message(
            f"Path resolution failed: {exc}", f"路径解析失败: {exc}", language
        )

    return resolved, None


def _load_history() -> list[dict[str, Any]]:
    if not HISTORY_FILE.exists():
        return []

    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FileOperationError(
            "history_unreadable",
            "Rename history is unreadable; the original has been preserved.",
            "无法读取重命名历史，原文件已保留。",
            detail=str(exc),
        ) from exc
    if not isinstance(data, list) or any(
        not isinstance(item, dict)
        or not all(isinstance(item.get(key), str) for key in ("old_path", "new_path"))
        for item in data
    ):
        raise FileOperationError(
            "history_corrupt",
            "Invalid rename history; the original has been preserved.",
            "重命名历史格式错误，原文件已保留。",
        )
    return data


def _save_history(history: list[dict[str, Any]]) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json(HISTORY_FILE, history)


def _record_rename(old_path: Path, new_path: Path) -> None:
    history = _load_history()
    history.append(
        {
            "old_path": str(old_path),
            "new_path": str(new_path),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "fingerprint": _fingerprint(new_path),
        }
    )
    _save_history(history)


def _file_info(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "path": str(path),
        "is_file": path.is_file(),
        "is_dir": path.is_dir(),
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(
            timespec="seconds"
        ),
        "accessed_at": datetime.fromtimestamp(stat.st_atime).isoformat(
            timespec="seconds"
        ),
        # ctime usually means creation time on Windows and metadata change time on Unix.
        "metadata_changed_or_created_at": datetime.fromtimestamp(
            stat.st_ctime
        ).isoformat(timespec="seconds"),
    }


def _safe_file_info(path: Path) -> dict[str, Any]:
    try:
        return _file_info(path)
    except OSError as exc:
        return {
            "name": path.name,
            "path": str(path),
            "error": "stat_failed",
            "message": str(exc),
        }


def _validate_new_filename(
    new_name: str, language: Literal["en", "zh"] = "en"
) -> str | None:
    if not new_name:
        return _message(
            "The new filename must not be empty.", "新文件名不能为空。", language
        )
    if new_name in {".", ".."}:
        return _message(
            "new_name must not equal a dot or two dots.",
            "new_name 不能是 . 或 ..。",
            language,
        )
    if Path(new_name).name != new_name or "/" in new_name or "\\" in new_name:
        return _message(
            "new_name must be a filename without a directory path.",
            "new_name 只能是文件名，不能包含目录路径。",
            language,
        )
    if new_name.endswith((" ", ".")):
        return _message(
            "For Windows compatibility, new_name must not end with a space or period.",
            "为兼容 Windows，new_name 不能以空格或句点结尾。",
            language,
        )
    if any(ord(ch) < 32 for ch in new_name):
        return _message(
            "new_name must not contain control characters.",
            "new_name 不能包含控制字符。",
            language,
        )
    if any(ch in WINDOWS_INVALID_CHARS for ch in new_name):
        return _message(
            'For Windows compatibility, new_name must not contain these characters: <>:"/\\|?*',
            '为兼容 Windows，new_name 不能包含以下字符: <>:"/\\|?*',
            language,
        )

    stem = new_name.split(".")[0].upper()
    if stem in WINDOWS_RESERVED_NAMES:
        return _message(
            f"For Windows compatibility, new_name must not use a reserved device name: {stem}",
            f"为兼容 Windows，new_name 不能使用保留设备名: {stem}",
            language,
        )

    return None


@mcp.tool()
@_tool_guard
def ping() -> dict[str, Any]:
    """
    探活：检查文件 MCP 服务状态，不改动任何文件。

    Check the file MCP server without changing files.
    """
    security_settings = _configured_transport_security()
    return {
        "ok": True,
        "server": "w-FileWorkbenchMCP",
        "platform": platform.system(),
        "host": mcp.settings.host,
        "port": mcp.settings.port,
        "path_separator": os.sep,
        "env_path_separator": os.pathsep,
        "filesystem_access": "unrestricted_by_server",
        "dns_rebinding_protection_enabled": security_settings.enable_dns_rebinding_protection,
        "allowed_hosts": security_settings.allowed_hosts,
        "allowed_origins": security_settings.allowed_origins,
    }


@mcp.tool()
@_tool_guard
def list_allowed_roots(language: Literal["en", "zh"] = "en") -> dict[str, Any]:
    """
    兼容性接口：可访问范围由操作系统权限决定，服务不维护根目录白名单。

    Compatibility endpoint: access is determined by OS permissions, without a root
    allowlist.
    """
    security_settings = _configured_transport_security()
    return {
        "ok": True,
        "platform": platform.system(),
        "host": mcp.settings.host,
        "port": mcp.settings.port,
        "env_path_separator": os.pathsep,
        "filesystem_access": "unrestricted_by_server",
        "dns_rebinding_protection_enabled": security_settings.enable_dns_rebinding_protection,
        "allowed_hosts": security_settings.allowed_hosts,
        "allowed_origins": security_settings.allowed_origins,
        "allowed_roots": [],
        "message": _message(
            "This server has no root-directory allowlist; access is determined by the service account permissions.",
            "本 MCP 不设置根目录白名单，访问范围由服务进程的系统账号权限决定。",
            language,
        ),
        "max_read_bytes": _max_read_bytes(),
    }


@mcp.tool()
@_tool_guard
def list_files(
    directory: str = ".",
    base_dir: str = "",
    language: Literal["en", "zh"] = "en",
    pattern: str = "*",
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    """
    列出服务账号有权访问的文件与子目录。
    language 取 en（默认）或 zh。

    List files and subdirectories accessible to the service account. language: en (default)
    or zh.
    """
    target_dir, error = _resolve_path(directory, base_dir, language)
    if error:
        return {"ok": False, "error": "invalid_path", "message": error}

    if not target_dir.exists():
        return {
            "ok": False,
            "error": "not_found",
            "message": _message(
                f"Directory not found: {target_dir}",
                f"目录不存在: {target_dir}",
                language,
            ),
        }
    if not target_dir.is_dir():
        return {
            "ok": False,
            "error": "not_a_directory",
            "message": _message(
                f"Not a directory: {target_dir}",
                f"不是目录: {target_dir}",
                language,
            ),
        }

    if not 1 <= limit <= 1000 or offset < 0:
        raise FileOperationError(
            "invalid_range",
            "limit must be 1..1000 and offset nonnegative.",
            "limit 必须为 1..1000，offset 不能为负数。",
        )
    entries = sorted(
        (p for p in target_dir.iterdir() if fnmatch.fnmatchcase(p.name, pattern)),
        key=lambda p: p.name.casefold(),
    )
    items = [_safe_file_info(p) for p in entries[offset : offset + limit]]

    return {
        "ok": True,
        "directory": str(target_dir),
        "count": len(items),
        "items": items,
        "total": len(entries),
        "next_offset": offset + len(items)
        if offset + len(items) < len(entries)
        else None,
    }


@mcp.tool()
@_tool_guard
def read_file(
    filepath: str,
    base_dir: str = "",
    language: Literal["en", "zh"] = "en",
    offset: int = 0,
    max_bytes: int = 0,
    encoding: Literal["auto", "utf-8", "gb18030"] = "auto",
) -> dict[str, Any]:
    """
    读取 UTF-8 或 GB18030 文本，不翻译文件内容。
    读取上限由 FILE_MCP_MAX_READ_BYTES 控制。
    language 取 en（默认）或 zh。

    Read UTF-8 or GB18030 text without translating its contents. Limited by
    FILE_MCP_MAX_READ_BYTES. language: en (default) or zh.
    """
    file_path, error = _resolve_path(filepath, base_dir, language)
    if error:
        return {"ok": False, "error": "invalid_path", "message": error}

    if not file_path.exists():
        return {
            "ok": False,
            "error": "not_found",
            "message": _message(
                f"File not found: {file_path}", f"文件不存在: {file_path}", language
            ),
        }
    if not file_path.is_file():
        return {
            "ok": False,
            "error": "not_a_file",
            "message": _message(
                f"Not a file: {file_path}", f"不是文件: {file_path}", language
            ),
        }

    if offset < 0 or max_bytes < 0 or encoding not in {"auto", "utf-8", "gb18030"}:
        raise FileOperationError(
            "invalid_range", "Invalid read range or encoding.", "读取范围或编码无效。"
        )
    max_bytes = min(max_bytes or _max_read_bytes(), _max_read_bytes())
    try:
        with file_path.open("rb") as file:
            size_bytes = os.fstat(file.fileno()).st_size
            file.seek(offset)
            raw = file.read(max_bytes + 1)
    except OSError as exc:
        return {"ok": False, "error": "read_failed", "message": str(exc)}

    truncated = len(raw) > max_bytes
    raw = raw[:max_bytes]

    for selected in ["utf-8", "gb18030"] if encoding == "auto" else [encoding]:
        try:
            decoder = codecs.getincrementaldecoder(selected)()
            content = decoder.decode(raw, final=not truncated)
            pending = len(decoder.getstate()[0])
            encoding = selected
            break
        except UnicodeDecodeError:
            continue
    else:
        raise FileOperationError(
            "binary_or_unknown_encoding",
            "Cannot decode this range; use a character-aligned offset and explicit encoding.",
            "无法解码此范围，请使用字符边界偏移及显式编码。",
        )
    consumed = len(raw) - pending
    if truncated and consumed == 0:
        raise FileOperationError(
            "read_limit_too_small",
            "The byte limit cannot fit one character.",
            "字节上限不足以容纳一个完整字符。",
        )

    return {
        "ok": True,
        "path": str(file_path),
        "encoding": encoding,
        "size_bytes": size_bytes,
        "returned_bytes_limit": max_bytes,
        "truncated": truncated,
        "content": content,
        "offset": offset,
        "next_offset": offset + consumed if truncated else None,
    }


@mcp.tool()
@_tool_guard
def get_file_info(
    filepath: str, base_dir: str = "", language: Literal["en", "zh"] = "en"
) -> dict[str, Any]:
    """
    获取文件或目录的基础元数据。
    language 取 en（默认）或 zh。

    Get basic file or directory metadata. language: en (default) or zh.
    """
    path, error = _resolve_path(filepath, base_dir, language)
    if error:
        return {"ok": False, "error": "invalid_path", "message": error}
    if not path.exists():
        return {
            "ok": False,
            "error": "not_found",
            "message": _message(
                f"Path not found: {path}", f"路径不存在: {path}", language
            ),
        }

    return {"ok": True, "info": _file_info(path)}


@mcp.tool()
@_tool_guard
def create_directory(
    directory: str,
    base_dir: str = "",
    parents: bool = True,
    language: Literal["en", "zh"] = "en",
) -> dict[str, Any]:
    """
    创建目录，默认自动创建缺失的上级目录。
    language 取 en（默认）或 zh。

    Create a directory, including missing parents by default. language: en (default) or zh.
    """
    target_dir, error = _resolve_path(directory, base_dir, language)
    if error:
        return {"ok": False, "error": "invalid_path", "message": error}

    if target_dir.exists():
        if target_dir.is_dir():
            return {
                "ok": True,
                "message": _message("Directory already exists", "目录已存在", language),
                "directory": str(target_dir),
                "created": False,
                "info": _file_info(target_dir),
            }
        return {
            "ok": False,
            "error": "target_exists",
            "message": _message(
                f"The target exists and is not a directory: {target_dir}",
                f"目标已存在且不是目录: {target_dir}",
                language,
            ),
        }

    try:
        target_dir.mkdir(parents=parents, exist_ok=False)
    except FileNotFoundError:
        return {
            "ok": False,
            "error": "parent_not_found",
            "message": _message(
                f"Parent directory not found: {target_dir.parent}",
                f"父目录不存在: {target_dir.parent}",
                language,
            ),
        }
    except OSError as exc:
        return {"ok": False, "error": "create_directory_failed", "message": str(exc)}

    return {
        "ok": True,
        "message": _message("Directory created successfully", "目录创建成功", language),
        "directory": str(target_dir),
        "created": True,
        "info": _file_info(target_dir),
    }


@mcp.tool()
@_tool_guard
def create_file(
    filepath: str,
    content: str,
    base_dir: str = "",
    overwrite: bool = False,
    create_parent_dirs: bool = True,
    language: Literal["en", "zh"] = "en",
) -> dict[str, Any]:
    """
    写入 UTF-8 文本，中英文内容原样保留。
    除非显式要求，不会覆盖已存在的文件。
    language 取 en（默认）或 zh。

    Write UTF-8 text, preserving English and Chinese content. Existing files are not
    overwritten unless requested. language: en (default) or zh.
    """
    file_path, error = _resolve_path(filepath, base_dir, language)
    if error:
        return {"ok": False, "error": "invalid_path", "message": error}

    if file_path.exists():
        if file_path.is_dir():
            return {
                "ok": False,
                "error": "target_is_directory",
                "message": _message(
                    f"The target is a directory: {file_path}",
                    f"目标是目录: {file_path}",
                    language,
                ),
            }
        if not overwrite:
            return {
                "ok": False,
                "error": "target_exists",
                "message": _message(
                    f"File already exists; set overwrite=true to replace it: {file_path}",
                    f"文件已存在，如需覆盖请设置 overwrite=true: {file_path}",
                    language,
                ),
            }

    if not file_path.parent.exists():
        if not create_parent_dirs:
            return {
                "ok": False,
                "error": "parent_not_found",
                "message": _message(
                    f"Parent directory not found: {file_path.parent}",
                    f"父目录不存在: {file_path.parent}",
                    language,
                ),
            }
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {
                "ok": False,
                "error": "create_parent_dirs_failed",
                "message": str(exc),
            }

    payload = content.encode("utf-8")
    try:
        _atomic_bytes(file_path, payload, overwrite)
    except OSError as exc:
        return {"ok": False, "error": "create_file_failed", "message": str(exc)}

    return {
        "ok": True,
        "message": _message("File created successfully", "文件创建成功", language)
        if not overwrite
        else _message("File written successfully", "文件写入成功", language),
        "path": str(file_path),
        "encoding": "utf-8",
        "size_bytes": file_path.stat().st_size,
        "overwritten": overwrite,
        "info": _file_info(file_path),
    }


@mcp.tool()
@_tool_guard
def delete_file(
    filepath: str,
    base_dir: str = "",
    language: Literal["en", "zh"] = "en",
) -> dict[str, Any]:
    """
    先备份再删除单个普通文件，返回 deletion_id。
    不支持递归删除；
    文件被外部并发修改时返回冲突。
    language 取 en（默认）或 zh。

    Back up and delete one regular file; return deletion_id. No recursive deletion.
    Concurrent external changes cause a conflict. language: en (default) or zh.
    """
    supplied = Path(filepath).expanduser()
    if base_dir:
        supplied = Path(base_dir).expanduser() / supplied
    if supplied.is_symlink():
        raise FileOperationError(
            "symlink_not_supported",
            "Deleting symlinks is not supported.",
            "不支持删除符号链接。",
        )
    path, error = _resolve_path(filepath, base_dir, language)
    if error:
        return {"ok": False, "error": "invalid_path", "message": error}
    if not path.is_file():
        raise FileOperationError(
            "not_a_file" if path.exists() else "not_found",
            "A regular file is required.",
            "目标必须是存在的普通文件。",
        )
    if any(
        _is_relative_to(path, root.resolve())
        for root in [DELETED_FILES_DIR, SNAPSHOTS_DIR]
    ):
        raise FileOperationError(
            "backup_path",
            "Backup files cannot be deleted through this tool.",
            "不能通过此工具删除备份文件。",
        )
    original = _fingerprint(path)
    _check_backup_capacity(original["size"] + 4096)
    deletion_id = uuid.uuid4().hex
    backup = DELETED_FILES_DIR / deletion_id
    backup.mkdir(parents=True)
    metadata = {
        "path": str(path),
        "deleted_at": datetime.now().isoformat(timespec="seconds"),
        "status": "pending",
        "fingerprint": original,
    }
    quarantine = path.with_name(".file-mcp-delete-" + deletion_id)
    _atomic_json(backup / "metadata.json", metadata)
    moved = False
    try:
        # Claim the directory entry first. New files at the old path are never unlinked.
        _move_no_replace(path, quarantine)
        moved = True
        if not _same_file(quarantine, original):
            raise FileOperationError(
                "file_changed", "File changed before deletion.", "删除前文件发生变化。"
            )
        shutil.copy2(quarantine, backup / "content")
        if (
            not _same_file(quarantine, original)
            or _fingerprint(backup / "content")["sha256"] != original["sha256"]
        ):
            raise FileOperationError(
                "file_changed",
                "File changed during backup; deletion cancelled.",
                "备份期间文件变化，已取消删除。",
            )
        metadata["status"] = "deleted"
        _atomic_json(backup / "metadata.json", metadata)
        quarantine.unlink()
    except (OSError, FileOperationError) as exc:
        rollback_error = None
        if moved and quarantine.exists():
            try:
                _move_no_replace(quarantine, path)
            except OSError as rollback:
                rollback_error = str(rollback)
        metadata["status"] = "failed"
        metadata["quarantine_path"] = str(quarantine) if quarantine.exists() else None
        try:
            _atomic_json(backup / "metadata.json", metadata)
        except OSError:
            pass
        return {
            "ok": False,
            "error": exc.code
            if isinstance(exc, FileOperationError)
            else "delete_failed",
            "message": _message(
                "Deletion failed; inspect the retained source or quarantine before retrying.",
                "删除失败，请检查保留的源文件或隔离文件后再重试。",
                language,
            ),
            "deletion_id": deletion_id,
            "path": str(path),
            "quarantine_path": metadata["quarantine_path"],
            "detail": str(exc),
            "rollback_error": rollback_error,
        }
    return {
        "ok": True,
        "path": str(path),
        "deletion_id": deletion_id,
        "message": _message(
            "File deleted; backup retained.", "文件已删除，备份已保留。", language
        ),
    }


@mcp.tool()
@_tool_guard
def restore_deleted_file(
    deletion_id: str, language: Literal["en", "zh"] = "en"
) -> dict[str, Any]:
    """
    校验备份后恢复被删除的文件，不会覆盖已存在的路径。
    language 取 en（默认）或 zh。

    Verify and restore a deletion backup without overwriting an existing path. language: en
    (default) or zh.
    """
    if not re.fullmatch(r"[0-9a-f]{32}", deletion_id):
        raise FileOperationError(
            "invalid_deletion_id", "Invalid deletion ID.", "删除记录 ID 无效。"
        )
    backup = _record_dir(DELETED_FILES_DIR, deletion_id)
    metadata = json.loads((backup / "metadata.json").read_text(encoding="utf-8"))
    if metadata.get("status", "deleted") not in {"deleted", "restored"}:
        raise FileOperationError(
            "incomplete_backup",
            "This deletion did not complete; inspect its source/quarantine.",
            "此删除未完成，请检查源文件或隔离文件。",
        )
    path = Path(metadata["path"])
    if os.path.lexists(path):
        raise FileOperationError(
            "target_exists",
            "The original path already exists.",
            "原路径已存在，未覆盖。",
        )
    checksum = metadata.get("fingerprint", {}).get("sha256")
    if not checksum:
        raise FileOperationError(
            "unverified_backup",
            "Legacy backup has no checksum; manual recovery is required.",
            "旧备份没有校验和，需要人工恢复。",
        )
    try:
        _copy_verified(backup / "content", path, checksum)
    except FileExistsError:
        raise FileOperationError(
            "target_exists",
            "The original path already exists.",
            "原路径已存在，未覆盖。",
        )
    metadata["status"] = "restored"
    try:
        history = _load_history()
        updated = False
        for item in history:
            if item["new_path"] == str(path) and item.get(
                "fingerprint"
            ) == metadata.get("fingerprint"):
                item["fingerprint"] = _fingerprint(path)
                updated = True
        if updated:
            _save_history(history)
        _atomic_json(backup / "metadata.json", metadata)
    except (OSError, FileOperationError) as exc:
        return {
            "ok": True,
            "path": str(path),
            "warning": "metadata_update_failed",
            "detail": str(exc),
            "message": _message(
                "File restored; backup status update failed.",
                "文件已恢复，但备份状态更新失败。",
                language,
            ),
        }
    return {
        "ok": True,
        "path": str(path),
        "message": _message("Deleted file restored.", "已恢复删除的文件。", language),
    }


@mcp.tool()
@_tool_guard
def rename_file(
    old_path: str,
    new_name: str,
    base_dir: str = "",
    language: Literal["en", "zh"] = "en",
) -> dict[str, Any]:
    """
    在文件所在目录内重命名，不会替换已存在的目标名。
    new_name 只能是文件名，不能是路径。
    支持中英文文件名。
    language 取 en（默认）或 zh。

    Rename a file within its current directory, without replacing an existing target.
    new_name must be a filename, not a path. English and Chinese names are supported.
    language: en (default) or zh.
    """
    old_file, error = _resolve_path(old_path, base_dir, language)
    if error:
        return {"ok": False, "error": "invalid_path", "message": error}

    if not old_file.exists():
        return {
            "ok": False,
            "error": "not_found",
            "message": _message(
                f"File not found: {old_file}", f"文件不存在: {old_file}", language
            ),
        }
    if not old_file.is_file():
        return {
            "ok": False,
            "error": "not_a_file",
            "message": _message(
                f"Not a file: {old_file}", f"不是文件: {old_file}", language
            ),
        }

    invalid_reason = _validate_new_filename(new_name, language)
    if invalid_reason:
        return {"ok": False, "error": "invalid_new_name", "message": invalid_reason}

    new_file = old_file.parent / new_name
    new_file_resolved, error = _resolve_path(str(new_file), language=language)
    if error:
        return {"ok": False, "error": "invalid_target_path", "message": error}

    if old_file == new_file_resolved:
        return {
            "ok": True,
            "message": _message(
                f"The file already has this name: {old_file.name}",
                f"文件已经是这个名字: {old_file.name}",
                language,
            ),
            "path": str(old_file),
        }

    if new_file_resolved.exists():
        return {
            "ok": False,
            "error": "target_exists",
            "message": _message(
                f"Target file already exists: {new_file_resolved}",
                f"目标文件已存在: {new_file_resolved}",
                language,
            ),
        }

    _load_history()  # Validate before changing the filesystem.
    fingerprint = _fingerprint(old_file)
    try:
        _move_no_replace(old_file, new_file_resolved)
    except OSError as exc:
        return {"ok": False, "error": "rename_failed", "message": str(exc)}

    try:
        _record_rename(old_file, new_file_resolved)
    except (OSError, FileOperationError) as exc:
        try:
            if not _same_file(new_file_resolved, fingerprint):
                raise OSError("Renamed file changed; automatic rollback stopped")
            _move_no_replace(new_file_resolved, old_file)
        except OSError as rollback:
            return {
                "ok": False,
                "error": "rename_partial",
                "path": str(new_file_resolved),
                "message": _message(
                    "Rename succeeded but history and rollback failed.",
                    "改名成功，但历史记录及回滚失败。",
                    language,
                ),
                "detail": str(exc),
                "rollback_error": str(rollback),
            }
        return {
            "ok": False,
            "error": "history_write_failed",
            "path": str(old_file),
            "rolled_back": True,
            "message": _message(
                "History write failed; rename was rolled back.",
                "历史写入失败，重命名已回滚。",
                language,
            ),
            "detail": str(exc),
        }
    return {
        "ok": True,
        "message": _message("Rename successful", "重命名成功", language),
        "old_path": str(old_file),
        "new_path": str(new_file_resolved),
    }


@mcp.tool()
@_tool_guard
def view_rename_history(
    filter_dir: str = "", language: Literal["en", "zh"] = "en"
) -> dict[str, Any]:
    """
    查看由本服务记录的重命名历史。
    language 取 en（默认）或 zh。

    View renames recorded by this server. language: en (default) or zh.
    """
    history = _load_history()
    if filter_dir:
        directory, error = _resolve_path(filter_dir, language=language)
        if error:
            return {"ok": False, "error": "invalid_path", "message": error}
        history = [
            item
            for item in history
            if _is_relative_to(Path(item.get("new_path", "")).resolve(), directory)
            or _is_relative_to(Path(item.get("old_path", "")).resolve(), directory)
        ]

    return {
        "ok": True,
        "history_file": str(HISTORY_FILE),
        "count": len(history),
        "items": history,
    }


@mcp.tool()
@_tool_guard
def restore_last_rename(
    filter_dir: str = "", language: Literal["en", "zh"] = "en"
) -> dict[str, Any]:
    """
    只恢复最近一次记录的重命名，可用 filter_dir 限定目录。
    language 取 en（默认）或 zh。

    Restore only the last recorded rename, optionally restricted to filter_dir. language: en
    (default) or zh.
    """
    history = _load_history()
    if not history:
        return {
            "ok": False,
            "error": "empty_history",
            "message": _message(
                "No rename history is available.", "没有重命名历史记录。", language
            ),
        }

    history_index = len(history) - 1
    if filter_dir:
        directory, error = _resolve_path(filter_dir, language=language)
        if error:
            return {"ok": False, "error": "invalid_path", "message": error}
        matching_indexes = [
            index
            for index, history_item in enumerate(history)
            if _is_relative_to(
                Path(history_item.get("new_path", "")).resolve(), directory
            )
            or _is_relative_to(
                Path(history_item.get("old_path", "")).resolve(), directory
            )
        ]
        if not matching_indexes:
            return {
                "ok": False,
                "error": "empty_history",
                "message": _message(
                    f"No rename history to restore in directory: {directory}",
                    f"目录没有可回退的重命名历史: {directory}",
                    language,
                ),
            }
        history_index = matching_indexes[-1]

    item = history[history_index]
    old_path = Path(item["old_path"]).resolve()
    new_path = Path(item["new_path"]).resolve()

    if not new_path.exists():
        return {
            "ok": False,
            "error": "not_found",
            "message": _message(
                f"Current file not found: {new_path}",
                f"当前文件不存在: {new_path}",
                language,
            ),
        }
    if old_path.exists():
        return {
            "ok": False,
            "error": "target_exists",
            "message": _message(
                f"The original path already exists: {old_path}",
                f"原路径已存在: {old_path}",
                language,
            ),
        }

    fingerprint = item.get("fingerprint")
    if not fingerprint:
        raise FileOperationError(
            "unverified_history",
            "Legacy rename has no file identity; automatic undo is disabled.",
            "旧改名记录没有文件身份信息，不能自动撤销。",
        )
    if not _same_file(new_path, fingerprint):
        raise FileOperationError(
            "file_changed",
            "The renamed file has changed or been replaced; undo cancelled.",
            "改名后的文件已变化或被替换，已取消撤销。",
        )
    try:
        _move_no_replace(new_path, old_path)
    except OSError as exc:
        return {"ok": False, "error": "restore_failed", "message": str(exc)}

    history.pop(history_index)
    try:
        _save_history(history)
    except OSError as exc:
        try:
            _move_no_replace(old_path, new_path)
        except OSError as rollback:
            return {
                "ok": False,
                "error": "restore_partial",
                "path": str(old_path),
                "message": _message(
                    "Undo succeeded but history and rollback failed.",
                    "撤销成功，但历史记录及回滚失败。",
                    language,
                ),
                "detail": str(exc),
                "rollback_error": str(rollback),
            }
        return {
            "ok": False,
            "error": "history_write_failed",
            "rolled_back": True,
            "message": _message(
                "History write failed; undo was rolled back.",
                "历史写入失败，撤销已回滚。",
                language,
            ),
            "detail": str(exc),
        }
    return {
        "ok": True,
        "message": _message(
            "The last rename has been restored", "最近一次重命名已恢复", language
        ),
        "restored_path": str(old_path),
    }


@mcp.tool()
@_tool_guard
def batch_rename_files(
    renames: list[dict[str, str]],
    base_dir: str = "",
    dry_run: bool = True,
    expected_plan_id: str = "",
    language: Literal["en", "zh"] = "en",
) -> dict[str, Any]:
    """
    单次请求最多预览 100 个同目录文件的重命名，正式执行必须带 plan_id。
    互换与链式改名会被拒绝。
    部分失败时返回已完成项，每个成功的重命名都有独立撤销记录。
    language 取 en（默认）或 zh。

    Preview up to 100 same-directory file renames per request. Apply requires plan_id.
    Swaps/chains are rejected. Partial failures return completed items; each successful
    rename has its own undo history. language: en (default) or zh.
    """
    if not 1 <= len(renames) <= 100:
        raise FileOperationError(
            "invalid_batch", "Provide 1..100 renames.", "请提供 1..100 个重命名项。"
        )
    plan, sources, targets = [], set(), set()
    for item in renames:
        source, error = _resolve_path(item["old_path"], base_dir, language)
        if error:
            return {"ok": False, "error": "invalid_path", "message": error}
        name = item["new_name"]
        invalid = _validate_new_filename(name, language)
        if invalid:
            return {"ok": False, "error": "invalid_new_name", "message": invalid}
        target = source.parent / name
        if (
            source == target
            or source in sources
            or target in targets
            or os.path.lexists(target)
        ):
            raise FileOperationError(
                "rename_conflict",
                "Duplicate, unchanged or existing rename target.",
                "重命名目标重复、未变化或已存在。",
            )
        sources.add(source)
        targets.add(target)
        plan.append(
            {
                "old_path": str(source),
                "new_path": str(target),
                "fingerprint": _fingerprint(source),
            }
        )
    if sources & targets:
        raise FileOperationError(
            "rename_conflict",
            "Rename chains and swaps are not supported.",
            "不支持链式改名或交换名称。",
        )
    plan_id = _plan_id(plan)
    if not dry_run and expected_plan_id != plan_id:
        raise FileOperationError(
            "plan_changed",
            "Preview renames and pass the current plan_id.",
            "请先预览重命名并传入当前 plan_id。",
            plan_id=plan_id,
        )
    completed = []
    if not dry_run:
        for item in plan:
            try:
                unchanged = _same_file(Path(item["old_path"]), item["fingerprint"])
            except (OSError, FileOperationError) as exc:
                return {
                    "ok": False,
                    "error": "batch_partial",
                    "completed": completed,
                    "detail": str(exc),
                    "message": _message(
                        "Batch stopped while checking a source.",
                        "检查源文件失败，批量改名已停止。",
                        language,
                    ),
                }
            if not unchanged:
                return {
                    "ok": False,
                    "error": "batch_partial",
                    "completed": completed,
                    "message": _message(
                        "Batch stopped because a source changed.",
                        "源文件发生变化，批量改名已停止。",
                        language,
                    ),
                }
            result = rename_file(
                item["old_path"], Path(item["new_path"]).name, language=language
            )
            if not result["ok"]:
                return {
                    "ok": False,
                    "error": "batch_partial",
                    "completed": completed,
                    "failed": result,
                    "message": _message(
                        "Batch stopped; earlier renames remain applied.",
                        "批量改名已停止，之前成功的改名仍保留。",
                        language,
                    ),
                }
            completed.append(result)
    return {
        "ok": True,
        "dry_run": dry_run,
        "plan_id": plan_id,
        "items": plan,
        "completed": completed,
        "message": _message(
            "Batch rename preview ready." if dry_run else "Batch rename completed.",
            "批量改名预览已生成。" if dry_run else "批量改名已完成。",
            language,
        ),
    }


@mcp.tool()
@_tool_guard
def list_deleted_files(
    filter_dir: str = "",
    limit: int = 100,
    offset: int = 0,
    language: Literal["en", "zh"] = "en",
) -> dict[str, Any]:
    """
    查询删除记录的 ID 与备份状态。
    损坏的记录会被报告出来，不会被隐藏。
    language 取 en（默认）或 zh。

    Find deletion IDs and backup states. Invalid records are reported, not hidden. language:
    en (default) or zh.
    """
    if not 1 <= limit <= 1000 or offset < 0:
        raise FileOperationError(
            "invalid_range", "Invalid pagination range.", "分页范围无效。"
        )
    directory = None
    if filter_dir:
        directory, error = _resolve_path(filter_dir, language=language)
        if error:
            return {"ok": False, "error": "invalid_path", "message": error}
    records, errors = [], []
    if DELETED_FILES_DIR.exists():
        for backup in sorted(DELETED_FILES_DIR.iterdir()):
            try:
                _record_dir(DELETED_FILES_DIR, backup.name)
                record = json.loads(
                    (backup / "metadata.json").read_text(encoding="utf-8")
                )
                if directory and not _is_relative_to(Path(record["path"]), directory):
                    continue
                records.append({"deletion_id": backup.name, **record})
            except (OSError, ValueError, KeyError, FileOperationError) as exc:
                errors.append({"record": backup.name, "detail": str(exc)})
    records.sort(
        key=lambda item: (item.get("deleted_at", ""), item["deletion_id"]), reverse=True
    )
    page = records[offset : offset + limit]
    return {
        "ok": True,
        "items": page,
        "count": len(page),
        "total": len(records),
        "errors": errors,
        "next_offset": offset + len(page)
        if offset + len(page) < len(records)
        else None,
    }


def _cleanup_plan(older_than_days: int):
    if older_than_days < 0:
        raise FileOperationError(
            "invalid_retention",
            "Retention days cannot be negative.",
            "保留天数不能为负数。",
        )
    cutoff = time.time() - older_than_days * 86400
    items = []
    for kind, root in [("deleted", DELETED_FILES_DIR), ("snapshot", SNAPSHOTS_DIR)]:
        if not root.exists():
            continue
        for path in sorted(root.iterdir()):
            _record_dir(root, path.name)
            info = path / ("metadata.json" if kind == "deleted" else "manifest.json")
            data = json.loads(info.read_text(encoding="utf-8"))
            if kind == "deleted" and data.get("status") not in {"deleted", "restored"}:
                continue  # Never purge pending/failed records needed for manual recovery.
            # Deletion age comes from the deletion timestamp, not a later restore.
            timestamp = datetime.fromisoformat(
                data["deleted_at"] if kind == "deleted" else data["created_at"]
            ).timestamp()
            if timestamp <= cutoff:
                files = sorted(path.rglob("*"))
                if any(p.is_symlink() for p in files):
                    raise FileOperationError(
                        "backup_symlink",
                        "Cleanup stopped at a backup symlink.",
                        "清理遇到备份符号链接，已停止。",
                    )
                items.append(
                    {
                        "kind": kind,
                        "record_id": path.name,
                        "bytes": sum(p.stat().st_size for p in files if p.is_file()),
                        "signature": [
                            (
                                str(p.relative_to(path)),
                                p.stat().st_size,
                                p.stat().st_mtime_ns,
                            )
                            for p in files
                            if p.is_file()
                        ],
                    }
                )
    return items


def _plan_id(plan) -> str:
    return hashlib.sha256(
        json.dumps(plan, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


@mcp.tool()
@_tool_guard
def cleanup_backups(
    older_than_days: int = 30,
    dry_run: bool = True,
    expected_plan_id: str = "",
    language: Literal["en", "zh"] = "en",
) -> dict[str, Any]:
    """
    预览备份保留期清理，正式执行必须带预览返回的 plan_id。
    彻底删除的是选中的备份，不是当前源文件。
    language 取 en（默认）或 zh。

    Preview backup retention cleanup. Apply requires the preview's plan_id. This permanently
    removes selected backups, not live source files. language: en (default) or zh.
    """
    plan = _cleanup_plan(older_than_days)
    plan_id = _plan_id(plan)
    if not dry_run and expected_plan_id != plan_id:
        raise FileOperationError(
            "plan_changed",
            "Preview cleanup and pass its current plan_id.",
            "请先预览清理并传入当前 plan_id。",
            plan_id=plan_id,
        )
    removed = []
    if not dry_run:
        for item in plan:
            root = DELETED_FILES_DIR if item["kind"] == "deleted" else SNAPSHOTS_DIR
            target = _record_dir(root, item["record_id"])
            # Resolve the exact deletion target and ensure it remains inside its backup root.
            if target.resolve().parent != root.resolve():
                raise FileOperationError(
                    "invalid_record", "Invalid backup path.", "备份路径无效。"
                )
            try:
                shutil.rmtree(target)
                removed.append(item["record_id"])
            except OSError as exc:
                return {
                    "ok": False,
                    "error": "cleanup_partial",
                    "removed": removed,
                    "detail": str(exc),
                    "message": _message(
                        "Cleanup stopped; inspect removed IDs.",
                        "清理已停止，请检查已删除的记录 ID。",
                        language,
                    ),
                }
    return {
        "ok": True,
        "dry_run": dry_run,
        "plan_id": plan_id,
        "items": plan,
        "total_bytes": sum(item["bytes"] for item in plan),
        "removed": removed,
        "message": _message(
            "Backup cleanup preview ready." if dry_run else "Backup cleanup completed.",
            "备份清理预览已生成。" if dry_run else "备份清理已完成。",
            language,
        ),
    }


def _walk_error(exc):
    raise exc


def _tree_state(directory: Path):
    if not directory.is_dir():
        raise FileOperationError(
            "not_a_directory",
            "An existing directory is required.",
            "目标必须是存在的目录。",
        )
    if any(
        _is_relative_to(root.resolve(), directory)
        or _is_relative_to(directory, root.resolve())
        for root in [DELETED_FILES_DIR, SNAPSHOTS_DIR]
    ):
        raise FileOperationError(
            "backup_overlap",
            "Snapshot directory overlaps server backup storage.",
            "快照目录与服务备份目录重叠。",
        )
    files, directories = {}, []
    maximum = _env_int("FILE_MCP_MAX_SNAPSHOT_ENTRIES", 10000, min_value=1)
    for parent, names, filenames in os.walk(
        directory, followlinks=False, onerror=_walk_error
    ):
        for name in sorted(names + filenames):
            path = Path(parent) / name
            if path.is_symlink() or (
                hasattr(path, "is_junction") and path.is_junction()
            ):
                raise FileOperationError(
                    "symlink_not_supported",
                    "Snapshots reject symlinks and junctions.",
                    "快照不支持符号链接或目录联接。",
                )
            relative = path.relative_to(directory).as_posix()
            if path.is_dir():
                directories.append(relative)
            else:
                files[relative] = _fingerprint(path)
            if len(files) + len(directories) > maximum:
                raise FileOperationError(
                    "snapshot_limit",
                    "Snapshot entry limit exceeded.",
                    "快照条目数超限。",
                )
    return {"files": files, "directories": sorted(directories)}


@mcp.tool()
@_tool_guard
def create_directory_snapshot(
    directory: str, base_dir: str = "", language: Literal["en", "zh"] = "en"
) -> dict[str, Any]:
    """
    在改动之前保存目录内递归的普通文件字节、哈希与空目录。
    拒绝符号链接/junction。
    language 取 en（默认）或 zh。

    Save recursive regular-file bytes, hashes and empty directories before changes.
    Symlinks/junctions are rejected. language: en (default) or zh.
    """
    root, error = _resolve_path(directory, base_dir, language)
    if error:
        return {"ok": False, "error": "invalid_path", "message": error}
    state = _tree_state(root)
    _check_backup_capacity(sum(item["size"] for item in state["files"].values()) + 4096)
    snapshot_id = uuid.uuid4().hex
    backup = SNAPSHOTS_DIR / snapshot_id
    backup.mkdir(parents=True)
    try:
        for index, (relative, info) in enumerate(sorted(state["files"].items())):
            blob = str(index)
            shutil.copy2(root / relative, backup / blob)
            if _fingerprint(backup / blob)["sha256"] != info["sha256"]:
                raise FileOperationError(
                    "file_changed",
                    "File changed during snapshot.",
                    "创建快照期间文件变化。",
                )
            info["blob"] = blob
        final_state = _tree_state(root)
        compared = {
            k: {key: value for key, value in v.items() if key != "blob"}
            for k, v in state["files"].items()
        }
        if (
            compared != final_state["files"]
            or state["directories"] != final_state["directories"]
        ):
            raise FileOperationError(
                "file_changed",
                "Directory changed during snapshot.",
                "创建快照期间目录变化。",
            )
        _atomic_json(
            backup / "manifest.json",
            {
                "root": str(root),
                "created_at": datetime.now().isoformat(timespec="seconds"),
                **state,
            },
        )
    except (OSError, FileOperationError):
        # Only the exact newly created backup directory is removed.
        if backup.resolve().parent == SNAPSHOTS_DIR.resolve():
            shutil.rmtree(backup)
        raise
    return {
        "ok": True,
        "snapshot_id": snapshot_id,
        "directory": str(root),
        "file_count": len(state["files"]),
        "message": _message(
            "Directory snapshot created.", "目录快照已创建。", language
        ),
    }


def _snapshot_manifest(snapshot_id: str):
    backup = _record_dir(SNAPSHOTS_DIR, snapshot_id)
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    root = Path(manifest["root"])
    if not root.is_absolute():
        raise ValueError("Snapshot root must be absolute")
    for relative in list(manifest["files"]) + manifest["directories"]:
        candidate = root / relative
        if (
            Path(relative).is_absolute()
            or not _is_relative_to(candidate.resolve(), root)
            or candidate.resolve() == root
        ):
            raise ValueError("Invalid snapshot relative path")
    for info in manifest["files"].values():
        if not re.fullmatch(r"[0-9]+", info["blob"]):
            raise ValueError("Invalid snapshot blob")
    return backup, manifest, root


@mcp.tool()
@_tool_guard
def list_directory_snapshots(language: Literal["en", "zh"] = "en") -> dict[str, Any]:
    """
    列出已保存的快照 ID 及其源目录。
    language 取 en（默认）或 zh。

    List saved snapshot IDs and source directories. language: en (default) or zh.
    """
    items, errors = [], []
    if SNAPSHOTS_DIR.exists():
        for entry in sorted(SNAPSHOTS_DIR.iterdir()):
            try:
                _, manifest, _ = _snapshot_manifest(entry.name)
                items.append(
                    {
                        "snapshot_id": entry.name,
                        "directory": manifest["root"],
                        "created_at": manifest["created_at"],
                        "file_count": len(manifest["files"]),
                    }
                )
            except (OSError, ValueError, KeyError, FileOperationError) as exc:
                errors.append({"snapshot_id": entry.name, "detail": str(exc)})
    return {"ok": True, "items": items, "errors": errors, "count": len(items)}


@mcp.tool()
@_tool_guard
def restore_directory_snapshot(
    snapshot_id: str,
    dry_run: bool = True,
    expected_plan_id: str = "",
    language: Literal["en", "zh"] = "en",
) -> dict[str, Any]:
    """
    预览或恢复目录快照，正式执行必须带预览的 plan_id。
    移除前会先备份已变更或新增的现存文件。
    这不是目录事务；
    部分失败时会报告已完成操作与删除 ID。
    language 取 en 或 zh。

    Preview or restore a directory snapshot. Apply requires the preview plan_id.
    Changed/added live files are backed up before removal. Not a directory transaction;
    partial failures report completed operations and deletion IDs. language: en or zh.
    """
    backup, manifest, root = _snapshot_manifest(snapshot_id)
    current = _tree_state(root)
    wanted = manifest["files"]
    added = sorted(set(current["files"]) - set(wanted))
    missing = sorted(set(wanted) - set(current["files"]))
    changed = sorted(
        name
        for name in set(wanted) & set(current["files"])
        if wanted[name]["sha256"] != current["files"][name]["sha256"]
    )
    extra_dirs = sorted(
        set(current["directories"]) - set(manifest["directories"]),
        key=lambda p: (-p.count("/"), p),
    )
    missing_dirs = sorted(
        set(manifest["directories"]) - set(current["directories"]),
        key=lambda p: (p.count("/"), p),
    )
    plan = {
        "snapshot_id": snapshot_id,
        "added": added,
        "missing": missing,
        "changed": changed,
        "extra_directories": extra_dirs,
        "missing_directories": missing_dirs,
        "wanted": wanted,
        "current": current,
    }
    plan_id = _plan_id(plan)
    if not dry_run and expected_plan_id != plan_id:
        raise FileOperationError(
            "plan_changed",
            "Preview restoration and pass its current plan_id.",
            "请先预览恢复并传入当前 plan_id。",
            plan_id=plan_id,
        )
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "plan_id": plan_id,
            "plan": plan,
            "message": _message(
                "Snapshot restore preview ready.", "快照恢复预览已生成。", language
            ),
        }
    _check_backup_capacity(
        sum(current["files"][name]["size"] for name in added + changed)
        + 4096 * len(added + changed)
    )
    completed, deletion_ids = [], []
    # Stage and verify all required bytes before changing the live directory.
    with tempfile.TemporaryDirectory(prefix="file-mcp-stage-") as stage_name:
        stage = Path(stage_name)
        for index, name in enumerate(missing + changed):
            _copy_verified(
                backup / wanted[name]["blob"],
                stage / str(index),
                wanted[name]["sha256"],
            )
        if _tree_state(root) != current:
            raise FileOperationError(
                "plan_changed",
                "Directory changed after preview.",
                "预览后目录发生变化。",
            )
        try:
            for name in added + changed:
                if not _same_file(root / name, current["files"][name]):
                    raise FileOperationError(
                        "file_changed",
                        "A live file changed before restoration.",
                        "恢复前当前文件发生变化。",
                    )
                result = delete_file(str(root / name), language=language)
                if not result["ok"]:
                    raise FileOperationError(
                        "delete_failed",
                        "Could not back up a live file.",
                        "无法备份当前文件。",
                        result=result,
                    )
                deletion_ids.append(result["deletion_id"])
                completed.append({"removed": name})
            for name in extra_dirs:
                (root / name).rmdir()
                completed.append({"removed_directory": name})
            for name in missing_dirs:
                (root / name).mkdir(parents=True, exist_ok=True)
                completed.append({"created_directory": name})
            for index, name in enumerate(missing + changed):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                _copy_verified(stage / str(index), target, wanted[name]["sha256"])
                completed.append({"restored": name})
            final = _tree_state(root)
            if (
                set(final["files"]) != set(wanted)
                or final["directories"] != manifest["directories"]
                or any(
                    final["files"][name]["sha256"] != info["sha256"]
                    for name, info in wanted.items()
                )
            ):
                raise FileOperationError(
                    "verification_failed",
                    "Final directory verification failed.",
                    "最终目录校验失败。",
                )
        except (OSError, FileOperationError) as exc:
            return {
                "ok": False,
                "error": "snapshot_restore_partial",
                "completed": completed,
                "deletion_ids": deletion_ids,
                "detail": str(exc),
                "failed": exc.details if isinstance(exc, FileOperationError) else {},
                "message": _message(
                    "Restore stopped; inspect completed operations and retained backups.",
                    "恢复已停止，请检查已完成操作及保留备份。",
                    language,
                ),
            }
    return {
        "ok": True,
        "dry_run": False,
        "completed": completed,
        "deletion_ids": deletion_ids,
        "message": _message(
            "Directory restored and hashes verified.",
            "目录已恢复并通过哈希校验。",
            language,
        ),
    }


async def _run_streamable_http_async() -> None:
    """Run Streamable HTTP with short shutdown-related uvicorn timeouts."""
    import uvicorn

    config = uvicorn.Config(
        mcp.streamable_http_app(),
        host=mcp.settings.host,
        port=mcp.settings.port,
        log_level=mcp.settings.log_level.lower(),
        timeout_keep_alive=_env_int(
            "FILE_MCP_UVICORN_TIMEOUT_KEEP_ALIVE",
            DEFAULT_UVICORN_TIMEOUT_KEEP_ALIVE,
            min_value=0,
        ),
        timeout_graceful_shutdown=_env_int(
            "FILE_MCP_UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN",
            DEFAULT_UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN,
            min_value=0,
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
#     "w-FileWorkbenchMCP": {
#       "name": "w-FileWorkbenchMCP",
#       "description": "Local file reading, listing and renaming MCP service",
#       "url": "http://<server-ip>:16682/mcp",
#       "type": "streamableHttp",
#       "isActive": true
#     }
#   }
# }
#
