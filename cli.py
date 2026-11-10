# -*- coding: utf-8 -*-
"""1.6 命令行入口：只做参数解析、校验、扫描编排与输出。

服务端能力由调用方注入（见 PLAN-1.6 §3「CLI 契约」）：本模块不导入
服务模块，也不含任何文件删除动作 —— 执行清理只能经注入的服务端单点。
"""

import argparse
import json
import os
import sys

CLI_MODE = "cli-oneclick"

_CLI_KEYS = {
    "--scan", "--dry-run", "--oneclick", "--yes", "--json",
    "--version", "-h", "--help",
}

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INVALID = 2
EXIT_OUTSIDE = 3
EXIT_UNCONFIRMED = 4


def is_cli_invocation(argv):
    """是否走 CLI 分支。空参数与 --takeover 仍走原来的 HTTP 服务。"""
    for arg in argv or ():
        if arg.split("=", 1)[0] in _CLI_KEYS:
            return True
    return False


def build_parser(version):
    p = argparse.ArgumentParser(
        prog="AppDataCleaner",
        description="AppData 磁盘清理助手 v%s 命令行模式（默认只读，删除需显式确认）" % version)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--scan", action="store_true", help="只扫描并输出摘要，不删除任何文件")
    g.add_argument("--dry-run", action="store_true", help="列出将清理的安全缓存项，不删除")
    g.add_argument("--oneclick", action="store_true", help="执行一键清理（必须同时给 --yes）")
    p.add_argument("--yes", action="store_true", help="显式确认执行删除；缺省时不删")
    p.add_argument("--root", help="扫描根目录，必须位于当前用户目录内")
    p.add_argument("--max-depth", type=int, default=3, help="扫描深度 1-6（默认 3）")
    p.add_argument("--fresh", action="store_true", help="强制全量扫描，不复用快照")
    p.add_argument("--hot-mb", type=float, default=50, help="热点阈值 MB（默认 50）")
    p.add_argument("--big-mb", type=float, default=100, help="大文件阈值 MB（默认 100）")
    p.add_argument("--json", action="store_true", help="单行 JSON 输出，供脚本解析")
    p.add_argument("--version", action="version", version="AppDataCleaner v%s" % version)
    p.add_argument("--takeover", action="store_true", help=argparse.SUPPRESS)
    return p


def _utf8():
    """Windows 控制台默认 GBK，中文输出统一 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _fmt_size(n):
    n = float(n or 0)
    for unit, div in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= div:
            return "%.1f %s" % (n / div, unit)
    return "%d B" % int(n)


def _emit(args, payload, lines):
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for line in lines:
            print(line)
    return payload["exitCode"]


def _fail(args, ver, action, root, msg, code):
    payload = {"version": ver, "action": action or "", "root": root,
               "ok": False, "exitCode": code, "error": msg}
    return _emit(args, payload, ["错误：%s" % msg])


def _sync_scan(app, root, depth, hot, big, fresh):
    """同步跑一次扫描，并把结果写进服务端会话状态（供枚举单点读取）。"""
    holder = {}

    def begin(ctx, r):
        holder["ctx"] = ctx
        app.STATE.begin(ctx, r)

    def finish(result, ex):
        if ex is not None:
            app.STATE.fail(getattr(holder.get("ctx"), "phase", "扫描失败"))
            return
        holder["result"] = result
        app.STATE.finish(result=result)

    try:
        app.scan.run_scan(root, depth, hot, big, on_begin=begin,
                          on_finish=finish, fresh=fresh,
                          cache_dir=app.DATA_DIR)
    except Exception:
        return None
    return holder.get("result")


def _emit_scan(args, ver, action, root, res):
    inc = res.get("incremental") or {}
    payload = {
        "version": ver, "action": action, "root": root,
        "ok": True, "exitCode": EXIT_OK, "error": "",
        "scan": {
            "totalSize": res.get("totalSize") or 0,
            "fileCount": res.get("fileCount") or 0,
            "elapsed": res.get("elapsed") or 0,
            "errorCount": res.get("errorCount") or 0,
            "scannedAt": res.get("scannedAt") or "",
            "incremental": bool(inc.get("used")),
        },
    }
    lines = [
        "AppData 磁盘清理助手 v%s · CLI" % ver,
        "动作    : 扫描",
        "根目录  : %s" % root,
        "扫描完成: %s / %d 个文件 / 耗时 %.1fs / 增量复用 %s" % (
            _fmt_size(res.get("totalSize")), res.get("fileCount") or 0,
            res.get("elapsed") or 0, "是" if inc.get("used") else "否"),
        "错误数  : %d" % (res.get("errorCount") or 0),
        "（未执行任何删除）",
    ]
    return _emit(args, payload, lines)


def _emit_plan(args, ver, action, root, items, needs_confirm):
    rows = [{"p": it.get("p") or "", "size": it.get("s") or 0,
             "level": "safe"} for it in items]
    total = sum(r["size"] for r in rows)
    code = EXIT_UNCONFIRMED if needs_confirm else EXIT_OK
    payload = {
        "version": ver, "action": action, "root": root,
        "ok": True, "exitCode": code, "error": "",
        "deleted": False, "needsConfirm": needs_confirm,
        "itemCount": len(rows), "totalSize": total, "items": rows,
    }
    lines = [
        "AppData 磁盘清理助手 v%s · CLI" % ver,
        "动作    : %s" % action,
        "根目录  : %s" % root,
        "将清理 %d 项，合计 %s：" % (len(rows), _fmt_size(total)),
    ]
    for r in rows:
        lines.append("  [safe] %-10s %s" % (_fmt_size(r["size"]), r["p"]))
    lines.append("缺少 --yes：未执行任何删除（退出码 %d）" % code
                 if needs_confirm else "（dry-run：未执行任何删除）")
    return _emit(args, payload, lines)


def _emit_result(args, ver, action, root, result):
    fails = result.get("failCount") or 0
    payload = {
        "version": ver, "action": action, "root": root,
        "ok": fails == 0, "exitCode": EXIT_FAILED if fails else EXIT_OK,
        "error": "", "deleted": True, "mode": CLI_MODE,
        "freedBySize": result.get("freedBySize") or 0,
        "okCount": result.get("okCount") or 0,
        "failCount": fails, "undoable": False,
        "results": result.get("results") or [],
    }
    lines = [
        "AppData 磁盘清理助手 v%s · CLI" % ver,
        "动作    : 一键清理（已显式确认）",
        "根目录  : %s" % root,
        "已清理 %d 项，释放 %s（默认移入回收站，可从回收站还原）" % (
            result.get("okCount") or 0, _fmt_size(result.get("freedBySize"))),
        "失败 %d 项" % fails,
    ]
    for r in (result.get("results") or []):
        if not r.get("ok"):
            lines.append("  [FAIL] %s  %s" % (r.get("p"), r.get("msg")))
    return _emit(args, payload, lines)


def run_cli(argv, app):
    """执行 CLI，返回进程退出码。app 为已初始化的服务端模块对象。"""
    _utf8()
    ver = app.APP_VERSION
    args = build_parser(ver).parse_args(argv)
    action = ("scan" if args.scan else
              "dry-run" if args.dry_run else
              "oneclick" if args.oneclick else None)
    if action is None:
        return _fail(args, ver, action, "",
                     "请指定 --scan / --dry-run / --oneclick 之一", EXIT_INVALID)

    root = os.path.abspath(args.root or app.DEFAULT_ROOT)
    if not app.inside(app.USER_HOME, root):
        return _fail(args, ver, action, root,
                     "只允许扫描当前用户目录下的路径：%s" % root, EXIT_OUTSIDE)
    if not os.path.isdir(root):
        return _fail(args, ver, action, root,
                     "目录不存在：%s" % root, EXIT_INVALID)

    depth = min(6, max(1, args.max_depth))
    hot = max(0.001, float(args.hot_mb))
    big = max(0.001, float(args.big_mb))
    try:
        os.makedirs(app.DATA_DIR, exist_ok=True)
        os.makedirs(app.LOG_DIR, exist_ok=True)
    except OSError as ex:
        return _fail(args, ver, action, root,
                     "数据目录不可写：%s" % ex, EXIT_INVALID)

    res = _sync_scan(app, root, depth, hot, big, args.fresh)
    if res is None:
        return _fail(args, ver, action, root, "扫描失败", EXIT_FAILED)

    if action == "scan":
        return _emit_scan(args, ver, action, root, res)

    items, err = app.plan_oneclick()
    if err:
        return _fail(args, ver, action, root, err, EXIT_FAILED)

    if action == "dry-run" or not args.yes:
        return _emit_plan(args, ver, action, root, items,
                          needs_confirm=(action == "oneclick" and not args.yes))

    result, err = app.run_oneclick(CLI_MODE)
    if err:
        return _fail(args, ver, action, root, err, EXIT_FAILED)
    return _emit_result(args, ver, action, root, result)
