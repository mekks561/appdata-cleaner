# -*- coding: utf-8 -*-
"""1.6 CLI 回归：契约、退出码、零删除、越界、真删与历史。

直接注入 server 模块调用 cli.run_cli，不启 HTTP 服务、不占端口。
数据/日志目录重定向到独立临时目录，避免污染项目 cache/logs。
server 延迟到 run() 才导入 —— 避免模块加载期的用户规则影响前面测试组。
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

import cli  # noqa: E402
from core import history  # noqa: E402
from tests.common import Checker, MB  # noqa: E402

TMP_ROOT = os.path.join(APP, "testdata", "_cli")
_SERVER = None


def _app():
    global _SERVER
    if _SERVER is None:
        import server
        _SERVER = server
    return _SERVER


class Sandbox(object):
    """把服务端的快照/日志目录重定向到独立临时目录。"""

    def __enter__(self):
        app = _app()
        self.tmp = tempfile.mkdtemp(prefix="adc-cli-data-")
        self.old = (app.DATA_DIR, app.LOG_DIR)
        app.DATA_DIR = os.path.join(self.tmp, "cache")
        app.LOG_DIR = os.path.join(self.tmp, "logs")
        return self

    def __exit__(self, *exc):
        app = _app()
        app.DATA_DIR, app.LOG_DIR = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)


def _fixture():
    """2 MB 的 safe 缓存 + 1 MB 的 data 级文件（后者不该被清理）。"""
    shutil.rmtree(TMP_ROOT, ignore_errors=True)
    safe = os.path.join(TMP_ROOT, "Local", "Temp")
    keep = os.path.join(TMP_ROOT, "Roaming", "OtherApp")
    os.makedirs(safe, exist_ok=True)
    os.makedirs(keep, exist_ok=True)
    with open(os.path.join(safe, "junk.bin"), "wb") as f:
        f.write(b"0" * (2 * MB))
    with open(os.path.join(keep, "settings.json"), "wb") as f:
        f.write(b"0" * MB)
    return safe, keep


def _tree(root):
    out = {}
    for base, _dirs, files in os.walk(root):
        for name in files:
            p = os.path.join(base, name)
            out[os.path.relpath(p, root)] = os.path.getsize(p)
    return out


def _capture(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.run_cli(argv, _app())
    raw = buf.getvalue().strip()
    try:
        data = json.loads(raw)
    except Exception:
        data = {}
    return code, data, raw


def _cli_history():
    return [r for r in history.read(_app().LOG_DIR, 100)
            if r.get("mode") == cli.CLI_MODE]


# ---------------- 入口分发与参数契约 ----------------

def test_invocation(c):
    c.eq("空参数 → 服务", cli.is_cli_invocation([]), False)
    c.eq("--takeover → 服务", cli.is_cli_invocation(["--takeover"]), False)
    c.eq("--scan → CLI", cli.is_cli_invocation(["--scan"]), True)
    c.eq("--dry-run --json → CLI", cli.is_cli_invocation(["--dry-run", "--json"]), True)
    c.eq("--oneclick --yes → CLI", cli.is_cli_invocation(["--oneclick", "--yes"]), True)
    c.eq("--version → CLI", cli.is_cli_invocation(["--version"]), True)
    c.eq("--root 单独给仍走服务", cli.is_cli_invocation(["--root", "X"]), False)


def test_parser(c):
    p = cli.build_parser("1.6.0")
    a = p.parse_args(["--scan", "--fresh", "--max-depth", "5", "--json"])
    c.eq("参数解析", (a.scan, a.fresh, a.max_depth, a.json, a.yes),
         (True, True, 5, True, False))
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        threw = False
        try:
            p.parse_args(["--scan", "--dry-run"])
        except SystemExit:
            threw = True
    c.ok("动作 flag 互斥", threw)
    with contextlib.redirect_stderr(io.StringIO()):
        threw = False
        try:
            p.parse_args(["--bogus"])
        except SystemExit:
            threw = True
    c.ok("未知参数报错", threw)
    c.eq("退出码常量", (cli.EXIT_OK, cli.EXIT_FAILED, cli.EXIT_INVALID,
                        cli.EXIT_OUTSIDE, cli.EXIT_UNCONFIRMED), (0, 1, 2, 3, 4))


def test_errors(c):
    with Sandbox():
        code, d, _ = _capture(["--yes", "--json"])
        c.eq("缺动作 → 2", code, 2)
        c.ok("缺动作有提示", "请指定" in d.get("error", ""))
        code, d, _ = _capture(["--scan", "--root", r"C:\Windows\Temp", "--json"])
        c.eq("越界 → 3", code, 3)
        c.ok("越界有提示", "只允许扫描" in d.get("error", ""))
        code, d, _ = _capture(["--scan", "--root",
                               os.path.join(TMP_ROOT, "_missing"), "--json"])
        c.eq("目录不存在 → 2", code, 2)
        c.ok("错误对象外层字段齐全",
             set(("version", "action", "root", "ok", "exitCode", "error")) <= set(d))


# ---------------- 扫描 / 预览 ----------------

def test_scan_and_dry_run(c):
    safe, _keep = _fixture()
    with Sandbox():
        before = _tree(TMP_ROOT)
        code, d, _ = _capture(["--scan", "--root", TMP_ROOT, "--json", "--fresh"])
        c.eq("--scan 退出码 0", code, 0)
        c.eq("--scan ok", d.get("ok"), True)
        c.eq("--scan 体积 3MB", (d.get("scan") or {}).get("totalSize"), 3 * MB)
        c.eq("--scan 文件数 2", (d.get("scan") or {}).get("fileCount"), 2)
        c.eq("--scan 全量不复用", (d.get("scan") or {}).get("incremental"), False)
        c.eq("--scan 文件树不变", _tree(TMP_ROOT), before)

        code, d, _ = _capture(["--dry-run", "--root", TMP_ROOT, "--json", "--fresh"])
        c.eq("--dry-run 退出码 0", code, 0)
        c.eq("dry-run 未删除", d.get("deleted"), False)
        c.eq("dry-run needsConfirm", d.get("needsConfirm"), False)
        c.eq("dry-run 1 项", d.get("itemCount"), 1)
        c.eq("dry-run 体积 2MB", d.get("totalSize"), 2 * MB)
        items = d.get("items") or []
        c.ok("dry-run 指向 safe 目录",
             bool(items) and items[0]["p"] == safe)
        c.eq("dry-run level", (items[0]["level"] if items else None), "safe")
        c.eq("dry-run 文件树不变", _tree(TMP_ROOT), before)
        plan, err = _app().plan_oneclick()
        c.eq("plan_oneclick 无错", err, None)
        c.eq("CLI 清单与服务端枚举一致",
             [it["p"] for it in (plan or [])], [it["p"] for it in items])

        code, _d, raw = _capture(["--scan", "--root", TMP_ROOT, "--fresh"])
        c.eq("人读输出退出码 0", code, 0)
        c.ok("人读输出中文提示", "扫描完成" in raw and "未执行任何删除" in raw)
        c.ok("人读输出不是 JSON", not raw.startswith("{"))


def test_unconfirmed(c):
    _safe, _keep = _fixture()
    with Sandbox():
        before = _tree(TMP_ROOT)
        code, d, _ = _capture(["--oneclick", "--root", TMP_ROOT, "--json", "--fresh"])
        c.eq("无 --yes 退出码 4", code, 4)
        c.eq("无 --yes needsConfirm", d.get("needsConfirm"), True)
        c.eq("无 --yes 未删除", d.get("deleted"), False)
        c.eq("无 --yes 文件树完全不变", _tree(TMP_ROOT), before)
        c.eq("无 --yes 不写 cli-oneclick 历史", _cli_history(), [])


# ---------------- 真删 / 历史 / 幂等 ----------------

def test_oneclick_yes(c):
    safe, keep = _fixture()
    with Sandbox():
        before = _tree(TMP_ROOT)
        code, d, _ = _capture(["--oneclick", "--yes", "--root", TMP_ROOT,
                               "--json", "--fresh"])
        c.eq("真删退出码 0", code, 0)
        c.eq("真删 deleted", d.get("deleted"), True)
        c.eq("真删 mode", d.get("mode"), cli.CLI_MODE)
        c.eq("真删 undoable（回收站还原）", d.get("undoable"), False)
        c.eq("真删释放 2MB", d.get("freedBySize"), 2 * MB)
        c.eq("真删成功 1 项", d.get("okCount"), 1)
        c.eq("真删失败 0 项", d.get("failCount"), 0)
        c.ok("safe 目录已移走", not os.path.isdir(safe))
        c.ok("data 级文件保留", os.path.isfile(os.path.join(keep, "settings.json")))
        after = _tree(TMP_ROOT)
        c.eq("体积恰好少 2MB", sum(after.values()),
             sum(before.values()) - 2 * MB)
        recs = _cli_history()
        c.eq("历史恰好 1 条", len(recs), 1)
        c.eq("历史释放量一致",
             (recs[0].get("freedBySize") if recs else None), 2 * MB)
        c.ok("历史条目含 1 个结果",
             bool(recs) and len(recs[0].get("items") or []) == 1)

        code2, d2, _ = _capture(["--dry-run", "--root", TMP_ROOT, "--json", "--fresh"])
        c.eq("清理后 dry-run 退出码 1", code2, 1)
        c.ok("清理后无可清项提示", "没有可清理" in d2.get("error", ""))


def run():
    c = Checker("CLI 模式 (1.6)")
    test_invocation(c)
    test_parser(c)
    test_errors(c)
    test_scan_and_dry_run(c)
    test_unconfirmed(c)
    test_oneclick_yes(c)
    return c.report()


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
