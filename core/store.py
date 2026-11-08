# -*- coding: utf-8 -*-
"""会话状态与一次性确认令牌。

1.0 的问题：STATE 是普通 dict、直接整体替换，多线程读写会读到半成品；
令牌用 md5(路径+时间) 生成，可预测。这里统一收口。
"""

import secrets
import threading
import time

TOKEN_TTL = 600  # 10 分钟


class _State(object):
    def __init__(self):
        self._lock = threading.RLock()
        self._ctx = None
        self._root = None
        self._result = None
        self._running = False
        self._phase = ""
        self._seq = 0

    def begin(self, ctx, root):
        with self._lock:
            self._ctx = ctx
            self._root = root
            self._result = None
            self._running = True
            self._phase = ctx.phase

    def finish(self, result=None, phase=None):
        with self._lock:
            if result is not None:
                self._result = result
                self._seq += 1
            if phase is not None:
                self._phase = phase
            self._running = False

    def fail(self, phase):
        with self._lock:
            self._phase = phase
            self._running = False

    def root(self, default=None):
        with self._lock:
            return self._root or default

    def result(self):
        with self._lock:
            return self._result

    def ctx(self):
        with self._lock:
            return self._ctx

    def running(self):
        with self._lock:
            return self._running

    def seq(self):
        """结果版本号：每产出一份新结果 +1（时间戳只有秒精度，靠它判定新鲜度）。"""
        with self._lock:
            return self._seq

    def status(self):
        with self._lock:
            ctx = self._ctx
            if ctx is None:
                return {"running": self._running, "phase": self._phase, "files": 0,
                        "bytes": 0, "hotCount": 0, "elapsed": 0, "done": False}
            elapsed = ctx.elapsed or round(time.time() - ctx.start, 1)
            return {
                "running": self._running,
                "phase": ctx.phase if self._running else self._phase or ctx.phase,
                "files": ctx.files,
                "bytes": ctx.bytes,
                "hotCount": ctx.total_hot,
                "elapsed": elapsed,
                "done": self._result is not None,
            }


class _Tokens(object):
    def __init__(self):
        self._lock = threading.Lock()
        self._items = {}

    def issue(self, paths, total):
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._purge_locked()
            self._items[token] = {
                "paths": sorted(paths),
                "total": total,
                "t": time.time(),
            }
        return token

    def consume(self, token):
        """取用并作废；过期或不存在返回 None。"""
        if not token:
            return None
        with self._lock:
            self._purge_locked()
            return self._items.pop(token, None)

    def _purge_locked(self):
        now = time.time()
        for k in [k for k, v in self._items.items() if now - v["t"] > TOKEN_TTL]:
            self._items.pop(k, None)


STATE = _State()
TOKENS = _Tokens()
