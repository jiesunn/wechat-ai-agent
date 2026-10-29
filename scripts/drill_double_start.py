"""演练：两个实例同时启动，第二个因单实例锁失败并以非零码退出。

用两个子进程真实争抢 fcntl.flock，data 目录是临时目录，不碰仓库 data/。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

HOLDER_CODE = """
import sys
import time

from wechat_agent.lock import InstanceLock

lock = InstanceLock(sys.argv[1])
lock.acquire()
print("LOCK_HELD", flush=True)
try:
    time.sleep(30)
finally:
    lock.release()
"""

SECOND_CODE = """
import sys

from wechat_agent.lock import acquire_lock_or_exit

acquire_lock_or_exit(sys.argv[1])
print("UNEXPECTED_SUCCESS")
"""


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        holder = subprocess.Popen(
            [sys.executable, "-c", HOLDER_CODE, str(data_dir)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout is not None
            line = holder.stdout.readline().strip()
            print(f"1. 第一个实例: {line}（PID {holder.pid}）")
            if line != "LOCK_HELD":
                assert holder.stderr is not None
                print(f"   第一个实例启动失败: {holder.stderr.read().strip()}")
                return 1

            second = subprocess.run(
                [sys.executable, "-c", SECOND_CODE, str(data_dir)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            print(f"2. 第二个实例退出码: {second.returncode}")
            print(f"3. 第二个实例 stderr: {second.stderr.strip()}")
            print(f"4. 锁文件: {data_dir / '.lock'}")

            assert second.returncode == 1
            assert "已有实例正在运行" in second.stderr
        finally:
            holder.terminate()
            try:
                holder.wait(timeout=5)
            except subprocess.TimeoutExpired:
                holder.kill()
                holder.wait()

    print("断言通过：第二个实例被单实例锁拒绝，退出码 1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
