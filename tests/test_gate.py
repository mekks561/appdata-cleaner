# -*- coding: utf-8 -*-
"""安全门禁矩阵 + 配置向前兼容回归。

不变量（任一破坏则版本不发布）集中在此，作为发版前的一键门禁：

  G1 仅 safe       —— 四级分类各自成立；autoclean 调度模块自身不含任何删除调用，
                      删除动作只能经注入的 run_fn（服务端枚举单点）发生；
  G2 7 天新鲜度    —— is_due 的 stale / no-result 守门矩阵；
  G3 默认关闭      —— DEFAULTS 默认关闭；坏值整段回退关闭；CLI 空参数不触发；
  G4 全部可撤销    —— RUN_MODE 固定为 auto-oneclick（历史可追溯），
                      maybe_run 只在 due 时调用执行函数；
  G5 无第二套删除路径（1.6）—— CLI 源码不含任何删除 API；
  G6 显式确认（1.6）      —— 无 --yes 时执行函数零调用、退出码 4。

配置向前兼容：旧 config 缺新字段自动补默认、坏值回退默认、旧文件加载不断服务。
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli  # noqa: E402
from core import autoclean, config, rules  # noqa: E402
from core.fsutil import inside  # noqa: E402
from tests.common import Checker  # noqa: E402

U = r"C:\Users\Administrator\AppData"
NOW = time.time()


def _ac(**kw):
    d = {"enabled": True, "interval": "daily", "hour": 0}
    d.update(kw)
    return {"autoClean": d}


# ---------------- 四条不变量 ----------------

def test_g1_only_safe(c):
    c.eq("Local\\Temp → safe", rules.classify(U + r"\Local\Temp")["level"], "safe")
    c.eq("Local\\NVIDIA → suspect", rules.classify(U + r"\Local\NVIDIA")["level"], "suspect")
    c.eq("Local\\Microsoft\\Edge → protected",
         rules.classify(U + r"\Local\Microsoft\Edge")["level"], "protected")
    c.eq("Roaming\\OtherApp → data",
         rules.classify(U + r"\Roaming\OtherApp\settings.json")["level"], "data")
    src = open(autoclean.__file__, encoding="utf-8").read()
    for call in ("os.remove", "os.unlink", "shutil.rmtree"):
        c.ok("autoclean 调度模块不含 %s（删除经注入 run_fn）" % call, call not in src)


def test_g2_freshness(c):
    due, reason = autoclean.is_due(_ac(), None, NOW - 8 * 86400)
    c.eq("扫描超 7 天 → stale（宁可不清）", (due, reason), (False, "stale"))
    due, reason = autoclean.is_due(_ac(), None, NOW - 7 * 86400 + 60)
    c.ok("7 天内不因新鲜度拦截", reason != "stale", reason)
    due, reason = autoclean.is_due(_ac(), None, None)
    c.eq("无扫描结果 → no-result", (due, reason), (False, "no-result"))


def test_g3_default_off(c):
    c.eq("DEFAULTS 默认关闭", config.DEFAULTS["autoClean"]["enabled"], False)
    cfg, errs, _w = config.validate({})
    c.eq("空配置回退默认（关闭）", cfg["autoClean"]["enabled"], False)
    cfg, errs, _w = config.validate({"autoClean": {"enabled": "yes"}})
    c.ok("坏 enabled → 整段回退关闭", bool(errs) and not cfg["autoClean"]["enabled"],
         cfg["autoClean"])
    c.eq("CLI 空参数不触发（仍走服务）", cli.is_cli_invocation([]), False)
    c.eq("CLI --takeover 不触发", cli.is_cli_invocation(["--takeover"]), False)


def test_g4_recoverable(c):
    c.eq("RUN_MODE 固定为历史模式", autoclean.RUN_MODE, "auto-oneclick")
    calls = []
    r = autoclean.maybe_run({"autoClean": {"enabled": False, "interval": "daily",
                                           "hour": 12}},
                            lambda: None, lambda: NOW,
                            lambda: calls.append(1) or (0, 0), now=NOW)
    c.eq("未启用不调用执行函数", (r["ran"], calls), (False, []))
    r = autoclean.maybe_run(_ac(), lambda: None, lambda: NOW - 9 * 86400,
                            lambda: calls.append(1) or (0, 0), now=NOW)
    c.eq("stale 不调用执行函数", (r["ran"], calls), (False, []))


# ---------------- G5 / G6（1.6 CLI） ----------------

def test_g5_cli_no_delete(c):
    """CLI 只能是薄参数层，删除必须全部经注入的服务端单点。"""
    src = open(cli.__file__, encoding="utf-8").read()
    for call in ("os.remove", "os.unlink", "shutil.rmtree", "force_remove",
                 "permanent(", "recycle(", "exec_action("):
        c.ok("CLI 源码不含 %s（无第二套删除路径）" % call, call not in src)


def _fake_app(tmp):
    """最小宿主：让 CLI 走到分发分支，精确观测执行函数是否被调用。"""
    calls = []

    class _State:
        def begin(self, ctx, r):
            pass

        def finish(self, result=None, phase=None):
            pass

        def fail(self, phase):
            pass

    class _Scan:
        @staticmethod
        def run_scan(root, depth, hot, big, on_begin=None, on_finish=None,
                     fresh=False, cache_dir=None):
            class _Ctx:
                phase = "完成"
            if on_begin:
                on_begin(_Ctx(), root)
            res = {"totalSize": 1024, "fileCount": 1, "elapsed": 0,
                   "errorCount": 0, "scannedAt": "", "incremental": {}}
            if on_finish:
                on_finish(res, None)
            return res

    class App:
        APP_VERSION = "1.6.0"
        # 门禁只验证「显式确认」分支；宿主用临时目录自身作为用户目录，
        # 避免系统 temp 的 8.3 短路径让 inside 误判越界（越界由 test_cli 真实路径覆盖）。
        USER_HOME = os.path.dirname(tmp)
        DEFAULT_ROOT = tmp
        DATA_DIR = os.path.join(tmp, "cache")
        LOG_DIR = os.path.join(tmp, "logs")
        STATE = _State()
        scan = _Scan()
        inside = staticmethod(inside)

        def plan_oneclick(self):
            return ([{"p": os.path.join(tmp, "Local", "Temp"),
                      "s": 1024, "level": "safe"}], None)

        def run_oneclick(self, mode):
            calls.append(mode)
            return ({"freedBySize": 1024, "okCount": 1, "failCount": 0,
                     "results": []}, None)

    app = App()
    app.calls = calls
    return app


def _cli_json(argv, app):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.run_cli(argv, app)
    try:
        payload = json.loads(buf.getvalue().strip())
    except Exception:
        payload = {}
    return code, payload


def test_g6_explicit_confirm(c):
    """缺 --yes 时绝不调用执行函数；给了 --yes 才走 cli-oneclick。"""
    tmp = tempfile.mkdtemp(prefix="acli16-")
    try:
        app = _fake_app(tmp)
        code, p = _cli_json(["--oneclick", "--root", tmp, "--json"], app)
        c.eq("无 --yes 退出码 4", code, 4)
        c.eq("无 --yes 未删除", p.get("deleted"), False)
        c.eq("无 --yes needsConfirm", p.get("needsConfirm"), True)
        c.eq("无 --yes 执行函数零调用", app.calls, [])
        code2, p2 = _cli_json(["--oneclick", "--yes", "--root", tmp, "--json"], app)
        c.eq("有 --yes 退出码 0", code2, 0)
        c.eq("有 --yes 调用一次且 mode 正确", app.calls, [cli.CLI_MODE])
        c.eq("有 --yes deleted", p2.get("deleted"), True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------- 配置向前兼容 ----------------

def test_compat(c):
    tmp = tempfile.mkdtemp(prefix="acfg15-")
    try:
        # 1.2 时代旧配置：无 dup / autoClean / warnGB
        old = {"port": 8765, "dest": "D:\\AppDataBackup", "rules": []}
        cfg, errs, _w = config.validate(old)
        c.eq("旧配置无错误", errs, [])
        c.eq("补齐 dup 默认", cfg["dup"], config.DEFAULTS["dup"])
        c.eq("补齐 autoClean 默认关闭", cfg["autoClean"]["enabled"], False)
        c.eq("补齐 warnGB 默认 10", cfg["warnGB"], 10)

        # 1.4 时代完整配置 roundtrip
        cfg, errs, _w = config.validate({
            "port": 8765, "dest": "D:\\AppDataBackup",
            "dup": {"minSizeMB": 50, "hashBudgetSec": 120},
            "autoClean": {"enabled": True, "interval": "weekly", "hour": 22},
            "warnGB": 0, "rules": [],
        })
        c.eq("1.4 配置 roundtrip",
             (cfg["warnGB"], cfg["autoClean"]["hour"], cfg["dup"]["minSizeMB"],
              cfg["autoClean"]["interval"]), (0, 22, 50, "weekly"))
        c.eq("1.4 配置无错误", errs, [])

        # 缺字段补默认：autoClean 只给 enabled
        cfg, errs, _w = config.validate({"autoClean": {"enabled": True}})
        c.eq("缺 interval/hour 补默认",
             (cfg["autoClean"]["enabled"], cfg["autoClean"]["interval"],
              cfg["autoClean"]["hour"]), (True, "daily", 12))
        c.eq("缺字段无错误", errs, [])

        # warnGB 坏值回退默认
        cfg, errs, _w = config.validate({"warnGB": "10"})
        c.ok("warnGB 字符串 → 报错并回退 10", bool(errs) and cfg["warnGB"] == 10, cfg["warnGB"])
        cfg, errs, _w = config.validate({"warnGB": -1})
        c.ok("warnGB 负数 → 报错并回退 10", bool(errs) and cfg["warnGB"] == 10)

        # 文件级：旧 config.json 加载后补默认，服务永不因缺字段起不来
        with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as f:
            f.write('{"port": 8900, "dest": "D:\\\\Backup", "rules": []}')
        cfg, warnings = config.load(tmp)
        c.eq("旧文件加载补 warnGB", cfg["warnGB"], 10)
        c.eq("旧文件加载补 autoClean 关闭", cfg["autoClean"]["enabled"], False)
        c.eq("旧文件加载补 dup", cfg["dup"], config.DEFAULTS["dup"])
        c.ok("旧文件加载无警告", warnings == [], warnings)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run():
    c = Checker("安全门禁矩阵 G1-G6 + 配置兼容")
    test_g1_only_safe(c)
    test_g2_freshness(c)
    test_g3_default_off(c)
    test_g4_recoverable(c)
    test_g5_cli_no_delete(c)
    test_g6_explicit_confirm(c)
    test_compat(c)
    return c.report()


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
