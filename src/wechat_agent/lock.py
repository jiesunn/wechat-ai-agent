"""单实例锁：同一数据目录只允许一个进程运行。"""

from __future__ import annotations

import fcntl
import os
import sys
from pathlib import Path
from typing import Final, TextIO

from wechat_agent.logging import ensure_runtime_dirs

LOCK_FILE_NAME: Final = ".lock"
LOCK_FILE_MODE: Final = 0o600


class LockUnavailableError(RuntimeError):
    """锁已被其他实例持有。"""


class InstanceLock:
    """基于 `fcntl.flock` 的非阻塞排他锁；锁文件保留，避免删除引发的竞态。"""

    def __init__(self, data_dir: str | Path = "data") -> None:
        self._data_dir = Path(data_dir)
        self._handle: TextIO | None = None

    @property
    def lock_path(self) -> Path:
        return self._data_dir / LOCK_FILE_NAME

    @property
    def acquired(self) -> bool:
        return self._handle is not None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        ensure_runtime_dirs(self._data_dir)
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, LOCK_FILE_MODE)
        os.fchmod(descriptor, LOCK_FILE_MODE)
        handle = os.fdopen(descriptor, "r+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            if isinstance(error, BlockingIOError):
                raise LockUnavailableError(self._owner_message()) from None
            raise
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None

    def _owner_message(self) -> str:
        try:
            owner = self.lock_path.read_text(encoding="utf-8").strip()
        except OSError:
            owner = ""
        detail = f"（PID {owner}）" if owner else ""
        return f"已有实例正在运行{detail}，锁文件：{self.lock_path}"

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()


def acquire_lock_or_exit(data_dir: str | Path = "data") -> InstanceLock:
    """获取锁；失败时输出明确提示并以退出码 1 结束进程。"""
    lock = InstanceLock(data_dir)
    try:
        lock.acquire()
    except LockUnavailableError as error:
        print(f"启动失败：{error}", file=sys.stderr)
        raise SystemExit(1) from None
    return lock
