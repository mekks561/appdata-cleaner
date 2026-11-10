# -*- coding: utf-8 -*-
"""V4-R1 定时自动清理安全缓存。

全路线最大的风险点（PLAN-1.4 风险 1）：唯一「无人值守执行删除」的功能。
四条不变量（任一被破坏则版本不发布）：
  1. 仅 safe     —— 清单来自与一键清理完全相同的服务端枚举单点；
  2. 新鲜度守门  —— 扫描结果超过 FRESH_SECONDS（7 天）宁可不清；
  3. 默认关闭    —— config.autoClean.enabled 必须显式开启，坏值回退关闭；
  4. 全部可撤销  —— 只走回收站，写历史（模式 auto-oneclick），历史页可追溯。

本模块只做「调度与守门」，不直接碰文件系统：
执行动作通过 run_fn 注入（server.py 传一键清理的服务端单点），
扫描新鲜度通过 scan_epoch_fn 注入 —— 测试用假函数即可覆盖全部守门矩阵。
"""

import threading
import time
from datetime import datetime

FRESH_SECONDS = 7 * 86400        # 扫描结果最长 7 天
CHECK_INTERVAL = 300             # 每 5 分钟醒来检查一次是否到期
RUN_MODE = "auto-oneclick"       # 历史记录模式，UI 可据此披露

_INTERVALS = ("daily", "weekly")


def _day_key(epoch):
    return time.strftime("%Y-%m-%d", time.localtime(epoch))


def _week_key(epoch):
    d = datetime.fromtimestamp(epoch)
    y, w, _ = d.isocalendar()
    return "%d-W%02d" % (y, w)


def _period_key(interval, epoch):
    return _day_key(epoch) if interval == "daily" else _week_key(epoch)


def is_due(cfg, last_epoch, scan_epoch, now=None):
    """守门判定。返回 (due: bool, reason: str)，reason 供 UI 披露。

    判定顺序即安全顺序：先看开关，再看数据新鲜度，最后看周期。
    catch-up 语义：服务启动时已过当日/当周计划时刻且本周期未跑过 → 立即补跑。
    只清 safe 级，补跑不会放大风险，且每次都有历史记录。
    """
    now = now if now is not None else time.time()
    ac = cfg.get("autoClean") or {}
    if not ac.get("enabled"):
        return False, "disabled"
    if ac.get("interval") not in _INTERVALS:
        return False, "bad-config"
    hour = ac.get("hour")
    if not isinstance(hour, int) or isinstance(hour, bool) or not (0 <= hour <= 23):
        return False, "bad-config"

    if scan_epoch is None:
        return False, "no-result"
    if now - scan_epoch > FRESH_SECONDS:
        return False, "stale"          # 7 天新鲜度守门：宁可不清不乱清

    # 未到当日计划时刻
    lt = time.localtime(now)
    if lt.tm_hour < hour:
        return False, "not-time"

    # 本周期（自然日 / ISO 周）已经跑过 → 幂等跳过
    if last_epoch is not None:
        now_key = _period_key(ac["interval"], now)
        last_key = _period_key(ac["interval"], last_epoch)
        if now_key == last_key:
            return False, "already"

    return True, "due"


def maybe_run(cfg, last_epoch_fn, scan_epoch_fn, run_fn, now=None):
    """调度器单步：检查守门，到期则执行一次。

    cfg          —— 当前配置 dict（实时读取，改设置立即生效）；
    last_epoch_fn —— 返回上次自动清理的 epoch（读历史），无则 None；
    scan_epoch_fn —— 返回当前扫描结果的 epoch，无结果则 None；
    run_fn       —— 执行函数，签名 () -> (freed:int, count:int)，
                    只在通过全部守门后才被调用。
    返回 {ran, reason, freed?, count?, at?}。
    """
    due, reason = is_due(cfg, last_epoch_fn(), scan_epoch_fn(), now)
    if not due:
        return {"ran": False, "reason": reason}
    freed, count = run_fn()
    return {"ran": True, "reason": "done", "freed": freed,
            "count": count, "at": int(time.time())}


class Scheduler(threading.Thread):
    """后台守护线程：每 CHECK_INTERVAL 醒来 maybe_run 一次。

    cfg_fn / last_epoch_fn / scan_epoch_fn / run_fn 均由 server.py 注入，
    本类不含任何业务规则 —— 守门逻辑全部在 is_due / maybe_run，可单测。
    """

    def __init__(self, cfg_fn, last_epoch_fn, scan_epoch_fn, run_fn,
                 on_done=None):
        super().__init__(daemon=True, name="auto-clean")
        self.cfg_fn = cfg_fn
        self.last_epoch_fn = last_epoch_fn
        self.scan_epoch_fn = scan_epoch_fn
        self.run_fn = run_fn
        self.on_done = on_done          # 回调(result dict)，供日志/状态披露
        self._stop = threading.Event()
        self.last_result = None
        self.last_error = None          # 最近一次调度异常（1.5 可观测性）

    def stop(self):
        self._stop.set()

    def step(self):
        """单次检查（供 run 循环与测试直接调用）。

        异常不向上抛、不带崩线程：记录到 last_error，UI 可追溯；
        守门逻辑（maybe_run）保持不变，不因记录错误而触发清理。
        """
        try:
            r = maybe_run(self.cfg_fn(), self.last_epoch_fn,
                          self.scan_epoch_fn, self.run_fn)
            if r.get("ran") or (self.on_done and r.get("reason") == "stale"):
                self.last_result = r
                if r.get("ran") and self.on_done:
                    self.on_done(r)
            return r
        except Exception as ex:
            self.last_error = {
                "at": int(time.time()),
                "type": type(ex).__name__,
                "message": str(ex)[:200],
            }
            return {"ran": False, "reason": "scheduler-error",
                    "error": str(ex)[:200]}

    def snapshot(self):
        """调度线程当前健康状态（供 /api/auto/status 披露）。"""
        return {
            "running": self.is_alive(),
            "lastError": self.last_error,
        }

    def run(self):
        # 启动后先等一个完整间隔再检查：预热扫描大概率还没跑完，
        # 上来就查只会得到 no-result；错开也让启动路径保持轻快。
        if self._stop.wait(CHECK_INTERVAL):
            return
        while not self._stop.is_set():
            self.step()
            self._stop.wait(CHECK_INTERVAL)
