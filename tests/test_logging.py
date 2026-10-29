"""结构化日志、脱敏过滤器与滚动策略的单元测试。"""

from __future__ import annotations

import io
import json
import logging
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from wechat_agent.logging import (
    LOG_FILE_NAME,
    close_logging,
    ensure_runtime_dirs,
    setup_logging,
)


@pytest.fixture
def log_env(
    tmp_path: Path,
) -> Iterator[tuple[logging.Logger, Path, io.StringIO]]:
    stream = io.StringIO()
    logger = setup_logging(
        data_dir=tmp_path / "data",
        level="info",
        max_bytes=1024 * 1024,
        backup_count=2,
        stream=stream,
    )
    try:
        yield logger, tmp_path / "data", stream
    finally:
        close_logging(logger)


def test_sensitive_fields_are_redacted(log_env: tuple[logging.Logger, Path, io.StringIO]) -> None:
    logger, data_dir, stream = log_env
    api_key = "sk-test-0123456789abcdefghijklmnopqrstuvwxyz"
    bot_token = "bot-token-0123456789abcdefghijklmnopqrstuvwxyz"

    logger.info(
        "llm request",
        extra={
            "api_key": api_key,
            "authorization": f"Bearer {bot_token}",
            "model": "test-model",
            "msg_id": "msg-1",
        },
    )

    content = (data_dir / "logs" / LOG_FILE_NAME).read_text(encoding="utf-8")
    stdout = stream.getvalue()

    assert api_key not in content
    assert bot_token not in content
    assert api_key not in stdout
    assert bot_token not in stdout
    assert "test-model" in content
    assert "msg-1" in content

    record = json.loads(content.strip().splitlines()[-1])
    assert record["level"] == "info"
    assert record["model"] == "test-model"
    assert record["msg_id"] == "msg-1"
    assert record["api_key"].startswith("sk-t")
    assert record["api_key"].endswith("wxyz")
    assert record["authorization"].startswith("Bearer ")
    assert bot_token not in record["authorization"]


def test_dict_args_are_redacted(log_env: tuple[logging.Logger, Path, io.StringIO]) -> None:
    logger, data_dir, stream = log_env
    api_key = "sk-args-0123456789abcdefghijklmnopqrstuvwxyz"

    logger.info("payload=%(api_key)s", {"api_key": api_key})

    content = (data_dir / "logs" / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert api_key not in content
    assert api_key not in stream.getvalue()


def test_rotation_creates_backup_file(tmp_path: Path) -> None:
    logger = setup_logging(
        data_dir=tmp_path / "data",
        max_bytes=400,
        backup_count=2,
        stream=io.StringIO(),
    )
    try:
        for index in range(30):
            logger.info("message %d %s", index, "x" * 80)
    finally:
        close_logging(logger)

    log_dir = tmp_path / "data" / "logs"
    current = log_dir / LOG_FILE_NAME
    backup = log_dir / f"{LOG_FILE_NAME}.1"

    assert current.exists()
    assert backup.exists()
    assert json.loads(backup.read_text(encoding="utf-8").strip().splitlines()[0])


def test_runtime_directories_are_created_with_0700(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"

    returned_data, returned_logs = ensure_runtime_dirs(data_dir)

    assert returned_data == data_dir
    assert returned_logs == data_dir / "logs"
    assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(returned_logs.stat().st_mode) == 0o700


def test_setup_logging_does_not_duplicate_handlers(tmp_path: Path) -> None:
    stream = io.StringIO()
    setup_logging(data_dir=tmp_path / "data", stream=stream)
    logger = setup_logging(data_dir=tmp_path / "data", stream=stream)
    try:
        logger.info("only once")
    finally:
        close_logging(logger)

    content = (tmp_path / "data" / "logs" / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert content.count("only once") == 1
    assert stream.getvalue().count("only once") == 1
