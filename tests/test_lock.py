"""单实例锁的获取、冲突与释放。"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from wechat_agent.lock import (
    LOCK_FILE_NAME,
    InstanceLock,
    LockUnavailableError,
    acquire_lock_or_exit,
)


def test_second_instance_cannot_acquire_same_lock(tmp_path: Path) -> None:
    first = InstanceLock(tmp_path)
    first.acquire()
    try:
        second = InstanceLock(tmp_path)
        with pytest.raises(LockUnavailableError) as excinfo:
            second.acquire()
        assert "已有实例正在运行" in str(excinfo.value)
    finally:
        first.release()


def test_lock_can_be_reacquired_after_release(tmp_path: Path) -> None:
    first = InstanceLock(tmp_path)
    first.acquire()
    first.release()

    second = InstanceLock(tmp_path)
    second.acquire()
    try:
        lock_path = tmp_path / LOCK_FILE_NAME
        assert second.acquired
        assert lock_path.read_text(encoding="utf-8").strip() == str(os.getpid())
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
    finally:
        second.release()

    assert second.acquired is False


def test_acquire_lock_or_exit_reports_conflict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    holder = InstanceLock(tmp_path)
    holder.acquire()
    try:
        with pytest.raises(SystemExit) as excinfo:
            acquire_lock_or_exit(tmp_path)
    finally:
        holder.release()

    assert excinfo.value.code == 1
    stderr = capsys.readouterr().err
    assert "启动失败" in stderr
    assert "已有实例正在运行" in stderr
