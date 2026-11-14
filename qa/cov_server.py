# -*- coding: utf-8 -*-
"""覆盖率专用启动器：以子进程方式跑 server，并在收到 Ctrl+Break 时先落盘。

为什么需要它：Windows 上 TerminateProcess 不会执行 atexit，coverage 数据
会丢失；而直接给 server.py 套 `coverage run` 时，Ctrl+Break 会让进程以
0xC000013A 硬退出。这里显式接管 SIGBREAK/SIGTERM，走 finally 保存。
"""

import os
import signal
import sys

import coverage

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP not in sys.path:
    sys.path.insert(0, APP)

cov = coverage.Coverage(config_file=True)
cov.start()

import server  # noqa: E402


def _stop(_signum, _frame):
    raise SystemExit(0)


if hasattr(signal, "SIGBREAK"):
    signal.signal(signal.SIGBREAK, _stop)
signal.signal(signal.SIGTERM, _stop)

try:
    server.main()
finally:
    cov.stop()
    cov.save()
    sys.stdout.flush()
