"""结构化日志：JSON 行写文件，人类可读摘要写 stdout，统一脱敏。"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final, TextIO

from wechat_agent.security import mask_mapping

LOGGER_NAME: Final = "wechat_agent"
LOG_FILE_NAME: Final = "app.log"
MAX_LOG_BYTES: Final = 10 * 1024 * 1024
LOG_BACKUP_COUNT: Final = 5
RUNTIME_DIR_MODE: Final = 0o700

_MANAGED_HANDLER_ATTR: Final = "_wechat_agent_managed_handler"
_VALID_LEVELS: Final = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_STANDARD_ATTRS: Final = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


class RedactionFilter(logging.Filter):
    """在格式化前用 security 的掩码规则处理字段与字典参数。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, Mapping):
            record.msg = mask_mapping(record.msg)
        if isinstance(record.args, Mapping):
            record.args = mask_mapping(record.args)
        extras = _extra_fields(record)
        for key, value in mask_mapping(extras).items():
            record.__dict__[key] = value
        return True


class JsonLineFormatter(logging.Formatter):
    """每行一个 JSON 对象，附加事件字段并保留异常栈。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": _format_timestamp(record.created),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(_extra_fields(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class HumanReadableFormatter(logging.Formatter):
    """stdout 摘要：时间、级别、模块、消息，以及 key=value 形式的事件字段。"""

    def __init__(self) -> None:
        super().__init__(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        extras = _extra_fields(record)
        if not extras:
            return rendered
        suffix = " ".join(f"{key}={value}" for key, value in extras.items())
        return f"{rendered} {suffix}"


def ensure_runtime_dirs(
    data_dir: str | Path = "data",
    log_dir: str | Path | None = None,
) -> tuple[Path, Path]:
    """创建数据目录与日志目录，并收紧为 0700（mkdirmode 受 umask 影响）。"""
    data_path = Path(data_dir)
    log_path = Path(log_dir) if log_dir is not None else data_path / "logs"
    for path in (data_path, log_path):
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(RUNTIME_DIR_MODE)
    return data_path, log_path


def setup_logging(
    *,
    data_dir: str | Path = "data",
    log_dir: str | Path | None = None,
    level: str = "info",
    max_bytes: int = MAX_LOG_BYTES,
    backup_count: int = LOG_BACKUP_COUNT,
    stream: TextIO | None = None,
) -> logging.Logger:
    """配置应用日志：文件 JSON 行 + stdout 摘要，重复调用会替换旧 handler。"""
    normalized_level = level.upper()
    if normalized_level not in _VALID_LEVELS:
        raise ValueError(f"不支持的日志级别: {level}")

    _, log_path = ensure_runtime_dirs(data_dir, log_dir)
    logger = logging.getLogger(LOGGER_NAME)
    close_logging(logger)
    logger.setLevel(normalized_level)
    logger.propagate = False

    redaction = RedactionFilter()
    file_handler = RotatingFileHandler(
        log_path / LOG_FILE_NAME,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(JsonLineFormatter())

    stream_handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    stream_handler.setFormatter(HumanReadableFormatter())

    for handler in (file_handler, stream_handler):
        handler.addFilter(redaction)
        setattr(handler, _MANAGED_HANDLER_ATTR, True)
        logger.addHandler(handler)

    return logger


def close_logging(logger: logging.Logger | None = None) -> None:
    """关闭并移除本模块创建的 handler，便于进程退出与测试隔离。"""
    target = logger if logger is not None else logging.getLogger(LOGGER_NAME)
    for handler in list(target.handlers):
        if getattr(handler, _MANAGED_HANDLER_ATTR, False):
            target.removeHandler(handler)
            handler.close()


def _extra_fields(record: logging.LogRecord) -> dict[str, object]:
    return {key: value for key, value in record.__dict__.items() if key not in _STANDARD_ATTRS}


def _format_timestamp(created: float) -> str:
    return datetime.fromtimestamp(created, tz=UTC).isoformat(timespec="milliseconds")
