# -*- coding: utf-8 -*-
"""V4-R1 定时自动清理安全缓存：守门矩阵 + API 闭环。

四条不变量逐条验证：
  · 仅 safe        —— API 闭环断言 FakeApp/Cache 被清、Data/OtherApp 原样；
  · 7 天新鲜度守门 —— 单元矩阵 stale 用例 + /api/auto/status 的 fresh 字段；
  · 默认关闭       —— 默认配置 enabled=False，check 返回 disabled；
  · 全部可撤销     —— 只走回收站（与一键清理同一 exec 路径），历史有记录。
"""

import json
import os
import shutil
import sys
import time

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

from core import autoclean, config  # noqa: E402
from tests.common import ROOT, Checker, fresh_testdata, get, post, scan  # noqa: E402

NOW = time.time()
LOG_DIR = os.path.join(APP, "logs")


def _drop_auto_history():
    """从全部历史 jsonl 里移除 auto-oneclick 行，让幂等守门归零。

    幂等以「历史里的最近一次自动清理」为准 —— 删掉记录即视为新周期开始，
    顺带验证了幂等判定确实读的是历史而不是内存。
    不只清当天文件：跨天残留的旧记录同样会干扰 lastRun 断言。
    """
    for name in os.listdir(LOG_DIR):
        if not name.startswith("clean-") or not name.endswith(".jsonl"):
            continue
        path = os.path.join(LOG_DIR, name)
        kept = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    if json.loads(line).get("mode") == autoclean.RUN_MODE:
                        continue
                except Exception:
                    pass
                kept.append(line)
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(kept)


def _at(hour, minute=0):
    """当天 hour:minute 的时间戳。"""
    lt = time.localtime(NOW)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hour, minute, 0, 0, 0, -1))


def cfg_ac(**kw):
    d = {"enabled": True, "interval": "daily", "hour": 12}
    d.update(kw)
    return {"autoClean": d}


# ---------------- 单元：守门矩阵 ----------------


def test_gates(c):
    due = lambda *a, **k: autoclean.is_due(*a, **k)  # noqa: E731

    d, r = due(cfg_ac(enabled=False), None, NOW)
    c.eq("未启用 → 不执行", (d, r), (False, "disabled"))

    d, r = due(cfg_ac(interval="monthly"), None, NOW)
    c.eq("非法 interval → bad-config", (d, r), (False, "bad-config"))
    d, r = due(cfg_ac(hour=24), None, NOW)
    c.eq("hour 越界 → bad-config", (d, r), (False, "bad-config"))
    d, r = due(cfg_ac(hour="12"), None, NOW)
    c.eq("hour 非整数 → bad-config", (d, r), (False, "bad-config"))

    d, r = due(cfg_ac(hour=0), None, None)
    c.eq("无扫描结果 → no-result", (d, r), (False, "no-result"))

    d, r = due(cfg_ac(), None, NOW - 8 * 86400)
    c.eq("扫描超 7 天 → stale（宁可不清）", (d, r), (False, "stale"))
    d, r = due(cfg_ac(), None, NOW - 7 * 86400 + 60)
    c.ok("扫描刚好 7 天内 → 新鲜度不拦（not-time 或 due）", r in ("not-time", "due"), r)

    lt = time.localtime(NOW)
    if 13 <= lt.tm_hour <= 22:
        # 用「下一个整点」做未来计划时刻：写死 hour=14 在 15 点之后会退化成
        # 「已过计划时刻」，导致本用例随运行时间漂移（1.6 全量回归时发现）。
        d, r = due(cfg_ac(hour=lt.tm_hour + 1), None, NOW)
        c.eq("未到计划时刻 → not-time", (d, r), (False, "not-time"))
        d, r = due(cfg_ac(hour=lt.tm_hour - 1), None, NOW)
        c.ok("已过计划时刻且从未跑过 → due", d, r)
    else:
        d, r = due(cfg_ac(hour=0), None, NOW)
        c.ok("hour=0 全天可跑 → due", d, r)

    # 幂等：本周期已跑过（hour=0 保证任何时刻都已过计划点）
    d, r = due(cfg_ac(hour=0), NOW - 60, NOW)
    c.eq("本周期已跑过 → already（幂等）", (d, r), (False, "already"))

    d, r = due(cfg_ac(hour=0), NOW - 2 * 86400, NOW)
    c.ok("上周期跑过、现在到点 → due", d, r)

    # weekly 语义：ISO 周（周一为一周起点；NOW - 6h 仍是今天，10 天前是上周）
    wcfg = cfg_ac(hour=0, interval="weekly")
    d, r = due(wcfg, NOW - 6 * 3600, NOW)
    c.eq("本周已跑 → already", (d, r), (False, "already"))
    d, r = due(wcfg, NOW - 10 * 86400, NOW)
    c.ok("上周跑过 → due", d, r)


def test_maybe_run(c):
    calls = []
    cfg = cfg_ac(hour=0)

    def run():
        calls.append(1)
        return 1234, 3

    # disabled：run_fn 绝不被调用
    r = autoclean.maybe_run(cfg_ac(enabled=False), lambda: None, lambda: NOW, run, now=NOW)
    c.eq("disabled 不执行", (r["ran"], calls), (False, []))
    r = autoclean.maybe_run(cfg, lambda: None, lambda: NOW - 9 * 86400, run, now=NOW)
    c.eq("stale 不执行", r["ran"], False)

    # due：执行一次，返回 freed/count
    r = autoclean.maybe_run(cfg, lambda: None, lambda: NOW, run, now=NOW)
    c.eq("due 执行", r["ran"], True)
    c.eq("返回释放量", r["freed"], 1234)
    c.eq("返回项数", r["count"], 3)
    c.eq("执行次数 1", len(calls), 1)

    # 幂等：last_epoch 更新后同周期再查 → skip
    r = autoclean.maybe_run(cfg, lambda: int(time.time()), lambda: NOW, run, now=NOW)
    c.eq("同周期第二次 → 不执行", r["reason"], "already")
    c.eq("执行次数仍 1", len(calls), 1)


# ---------------- 单元：调度异常可见化（1.5） ----------------


def test_scheduler_error(c):
    cfg = cfg_ac(hour=0)

    def boom():
        raise RuntimeError("注入失败")

    sched = autoclean.Scheduler(lambda: cfg, lambda: None, lambda: NOW, boom)
    r = sched.step()
    c.eq("run_fn 异常 → reason=scheduler-error", r.get("reason"), "scheduler-error")
    c.ok("异常不向上抛", r.get("ran") is False, r)
    c.eq("last_error.type", sched.last_error.get("type"), "RuntimeError")
    c.ok("last_error.message 保留", "注入失败" in sched.last_error.get("message", ""))
    c.ok("snapshot.lastError 可披露", bool(sched.snapshot().get("lastError")))

    sched2 = autoclean.Scheduler(lambda: cfg_ac(enabled=False), lambda: None, lambda: NOW, boom)
    r2 = sched2.step()
    c.eq("正常 step 不调用 run_fn", r2.get("reason"), "disabled", r2)
    c.ok("正常 step 无 last_error", sched2.last_error is None)


# ---------------- 单元：config 校验 ----------------


def test_config(c):
    cfg, _e, _w = config.validate({})
    c.eq(
        "缺省 autoClean → 默认关闭",
        (cfg["autoClean"]["enabled"], cfg["autoClean"]["interval"], cfg["autoClean"]["hour"]),
        (False, "daily", 12),
    )
    cfg, errors, _w = config.validate({"autoClean": {"enabled": "yes"}})
    c.ok(
        "enabled 非布尔 → 整段回退默认（关闭）",
        errors and cfg["autoClean"] == config.DEFAULTS["autoClean"],
        errors,
    )
    cfg, errors, _w = config.validate({"autoClean": {"interval": "hourly"}})
    c.ok("interval 非法 → 报错回退", errors and not cfg["autoClean"]["enabled"], errors)
    cfg, errors, _w = config.validate({"autoClean": {"hour": 24}})
    c.ok("hour=24 → 报错回退", bool(errors), errors)
    cfg, errors, _w = config.validate(
        {"autoClean": {"enabled": True, "interval": "weekly", "hour": 22}}
    )
    c.eq(
        "合法值 roundtrip",
        (cfg["autoClean"]["enabled"], cfg["autoClean"]["interval"], cfg["autoClean"]["hour"]),
        (True, "weekly", 22),
    )
    c.eq("合法值无错误", errors, [])


# ---------------- API 闭环 ----------------


def test_api(c):
    st, s = get("/api/auto/status")
    c.eq("auto/status 200", st, 200)
    c.eq("默认未启用", s.get("enabled"), False)
    c.ok("默认 healthy（无调度异常记录）", s.get("healthy") is True, s)
    c.ok("默认无 lastError", s.get("lastError") is None, s)

    st, r = post("/api/auto/check", {})
    c.eq("未启用时 check → 不执行", (st, r.get("ran"), r.get("reason")), (200, False, "disabled"))

    # —— 1.5：执行失败可见。有扫描结果但无 safe 项时，
    #    run_oneclick 返回错误，_auto_run_fn 写历史 error，状态行可披露 ——
    _drop_auto_history()
    st, _b = post(
        "/api/config",
        {
            "port": 8765,
            "dest": "D:\\AppDataBackup",
            "autoClean": {"enabled": True, "interval": "daily", "hour": 0},
            "rules": [],
        },
    )
    c.eq("开启自动清理 → 保存 200", st, 200, _b)
    empty_dir = os.path.join(ROOT, "_auto_empty")
    os.makedirs(empty_dir, exist_ok=True)
    try:
        scan(root=empty_dir, fresh=True)
        st, r = post("/api/auto/check", {})
        c.eq("无 safe 项也算执行过（不静默）", r.get("ran"), True, r)
        st, s = get("/api/auto/status")
        c.ok("lastRun.error 披露失败原因", bool((s.get("lastRun") or {}).get("error")), s)
    finally:
        shutil.rmtree(empty_dir, ignore_errors=True)
    _drop_auto_history()

    # 开启（hour=0 保证到点）并准备新鲜扫描
    _cfg, _w = None, None
    st, body = post(
        "/api/config",
        {
            "port": 8765,
            "dest": "D:\\AppDataBackup",
            "autoClean": {"enabled": True, "interval": "daily", "hour": 0},
            "rules": [],
        },
    )
    c.eq("开启自动清理 → 保存 200", st, 200, body)

    st, s = get("/api/auto/status")
    c.eq("status 反映已启用", s.get("enabled"), True)
    c.eq("nextRun 已给出", isinstance(s.get("nextRun"), int), True, s)

    cache = os.path.join(ROOT, "Local", "Temp")  # 命中内置 safe 规则
    suspect = os.path.join(ROOT, "Local", "FakeApp", "Cache")  # 启发式 suspect，绝不清
    data_file = os.path.join(ROOT, "Local", "FakeApp", "Data", "db.sqlite")
    other = os.path.join(ROOT, "Roaming", "OtherApp", "settings.json")

    # 回归实例可能残留旧扫描结果与旧执行记录，先归零再铺新鲜 testdata
    _drop_auto_history()
    fresh_testdata()
    os.makedirs(os.path.join(cache, "sub"), exist_ok=True)
    with open(os.path.join(cache, "sub", "junk.bin"), "wb") as f:
        f.write(b"0" * (12 * 1024 * 1024))
    scan(fresh=True)

    st, r = post("/api/auto/check", {})
    c.eq("到点执行", r.get("ran"), True, r)
    c.eq("释放量 > 0", (r.get("freed") or 0) > 0, True, r)
    if r.get("error"):
        c.ok("执行无错误", False, r["error"])
    c.ok("Local/Temp 已清入回收站", not os.path.isdir(cache))
    c.ok("疑似目录 FakeApp/Cache 原样（仅 safe 不变量）", os.path.isdir(suspect))
    c.ok("Data/db.sqlite 原样（非 safe 不动）", os.path.isfile(data_file))
    c.ok("OtherApp/settings.json 原样", os.path.isfile(other))

    # 幂等：同周期再 check → already
    st, r2 = post("/api/auto/check", {})
    c.eq("第二次 check → already", (r2.get("ran"), r2.get("reason")), (False, "already"), r2)

    # 历史与状态披露
    st, hist = get("/api/history?limit=20")
    modes = [x.get("mode") for x in hist.get("items", [])]
    c.ok("历史有 auto-oneclick 记录", "auto-oneclick" in modes, modes[:5])
    st, s = get("/api/auto/status")
    c.ok("status.lastRun 已披露", bool(s.get("lastRun")), s)
    c.ok("lastRun.freed > 0", (s.get("lastRun") or {}).get("freed", 0) > 0)

    # 关闭后再 check → disabled
    st, _b = post(
        "/api/config",
        {
            "port": 8765,
            "dest": "D:\\AppDataBackup",
            "autoClean": {"enabled": False, "interval": "daily", "hour": 12},
            "rules": [],
        },
    )
    st, r = post("/api/auto/check", {})
    c.eq("关闭后 check → disabled", r.get("reason"), "disabled", r)

    # 还原演示用的测试配置文件为默认（不留 enabled 状态在仓库运行目录）
    st, _b = post(
        "/api/config",
        {
            "port": 8765,
            "dest": "D:\\AppDataBackup",
            "rules": [],
        },
    )


def run():
    c = Checker("自动清理 V4-R1")
    base = os.path.join(ROOT, "_auto_unit")
    try:
        test_gates(c)
        test_maybe_run(c)
        test_scheduler_error(c)
        test_config(c)
        test_api(c)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return c.report()


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
