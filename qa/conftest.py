# -*- coding: utf-8 -*-
"""pytest 驱动层的会话级夹具：整个会话只拉起一次服务。

与 run_tests.py 使用同一套环境变量；若已有实例在跑则直接复用。
设置 AC_COVERAGE=1 时用 `coverage run --parallel-mode` 包裹子进程，
把 HTTP 路径上 server/core 的真实覆盖率并入最终报告。
"""

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

import pytest

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP not in sys.path:
    sys.path.insert(0, APP)

from tests.common import BASE  # noqa: E402


def _alive():
    try:
        with urllib.request.urlopen(BASE + "/api/info", timeout=1.0) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


@pytest.fixture(scope="session")
def server():
    if _alive():
        yield BASE
        return

    env = dict(
        os.environ,
        APP_NO_BROWSER="1",
        APP_NO_GUI="1",
        APP_NO_WARMUP="1",
        APP_NO_AUTOCLEAN="1",
        PYTHONUTF8="1",
    )
    cmd = [sys.executable]
    popen_kwargs = {}
    if os.environ.get("AC_COVERAGE") == "1":
        # 用覆盖率启动器包住 server：它接管 Ctrl+Break，退出前把数据落盘
        cmd += [os.path.join("qa", "cov_server.py")]
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        cmd += ["server.py"]

    proc = subprocess.Popen(
        cmd,
        cwd=APP,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        **popen_kwargs,
    )
    try:
        info = None
        for _ in range(120):
            time.sleep(0.25)
            info = _alive()
            if info:
                break
        if not info:
            raise RuntimeError("服务启动失败（pytest 会话夹具）")
        yield BASE
    finally:
        if popen_kwargs and proc.poll() is None:
            try:
                proc.send_signal(signal.CTRL_BREAK_EVENT)
                proc.wait(timeout=8)
            except Exception:
                proc.kill()
        else:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
