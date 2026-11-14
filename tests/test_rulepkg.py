# -*- coding: utf-8 -*-
"""1.7 规则包导入/导出：包校验矩阵、导入替换语义、安全不变量、往返一致。

分层：
  单元 —— rulepkg.validate_package 的整包/逐条校验矩阵（不起服务）；
  G1 专项 —— 导入的 safe 规则不能把内置保护目录标成可删（classify 优先级）；
  API  —— 导出/导入端点闭环：替换、持久化、坏包拒绝、其余配置段原样。

API 段会修改 CFG["rules"]，开头快照、finally 恢复整份配置。
"""

import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config, rulepkg, rules  # noqa: E402
from tests.common import APP, BASE, Checker, get, post  # noqa: E402

PKG = rulepkg.PACKAGE_FORMAT
VER = rulepkg.PACKAGE_VERSION


def _pkg(rules_, **kw):
    d = {
        "format": PKG,
        "version": VER,
        "exportedAt": "2026-11-11T09:00:00",
        "appVersion": "1.6.0",
        "count": len(rules_),
        "rules": rules_,
    }
    d.update(kw)
    return d


def _rule(segs, level, note="测试规则说明", vendor="自定义"):
    return {"segments": segs, "level": level, "note": note, "vendor": vendor}


# ---------------- 单元：包校验矩阵 ----------------


def test_package_matrix(c):
    good = [_rule(["local", "mytool", "cache"], "safe"), _rule(["roaming", "legacy"], "protected")]
    valid, errs, warns = rulepkg.validate_package(_pkg(good))
    c.eq("合法包通过", (len(errs), len(valid)), (0, 2))
    c.eq("合法包无警告", warns, [])

    valid, errs, _w = rulepkg.validate_package({"rules": []})
    c.ok("缺 format → 拒绝", errs and not valid, errs)
    valid, errs, _w = rulepkg.validate_package(_pkg([], format="other-rules"))
    c.ok("format 名不对 → 拒绝", errs and not valid, errs)
    valid, errs, _w = rulepkg.validate_package(_pkg([], version=2))
    c.ok("version=2 → 拒绝", errs and not valid, errs)
    valid, errs, _w = rulepkg.validate_package(_pkg([], version="1"))
    c.ok("version 字符串 → 拒绝", errs and not valid, errs)
    valid, errs, _w = rulepkg.validate_package(_pkg([], rules="x"))
    c.ok("rules 非数组 → 拒绝", errs and not valid, errs)
    valid, errs, _w = rulepkg.validate_package(
        _pkg([_rule(["local", "x"], "safe")] * (config.MAX_RULES + 1))
    )
    c.ok("超过 64 条 → 拒绝", errs and not valid, errs)
    valid, errs, _w = rulepkg.validate_package(None)
    c.ok("非 dict → 拒绝", errs and not valid, errs)
    valid, errs, _w = rulepkg.validate_package(_pkg([], count=3))
    c.ok("count 不符 → 仅警告", not errs and len(_w) == 1, _w)
    valid, errs, _w = rulepkg.validate_package(_pkg([], count=True))
    c.ok("count 为布尔 → 忽略", not errs, errs)

    # 逐条降级：非法条目进警告、合法条目生效
    mixed = [
        _rule(["local", "ok"], "safe"),
        _rule(["local", "x"], "danger"),  # level 非法
        _rule(["local", "x"], "safe", note=""),  # note 缺失
        _rule(["local", "../evil"], "safe"),  # 段含分隔符
        _rule([], "safe"),  # 段为空
        "not-a-dict",
    ]  # 条目非对象
    valid, errs, warns = rulepkg.validate_package(_pkg(mixed))
    c.eq("非法条目被丢弃且整包可过", (len(errs), len(valid)), (0, 1))
    c.eq("逐条警告数", len(warns), 5, warns)
    c.eq("合法条目内容保留", valid[0]["segments"], ["local", "ok"])

    # 向前兼容：未知字段忽略
    r = _rule(["local", "future"], "safe")
    r["newField"] = {"whatever": 1}
    valid, errs, _w = rulepkg.validate_package(_pkg([r], extraTop="ignored"))
    c.eq("未知字段忽略", (len(errs), valid[0]["segments"]), (0, ["local", "future"]))


# ---------------- 单元：G1 专项（提权必须失败） ----------------


def test_no_elevation(c):
    USER_APPDATA = os.path.join(os.path.expanduser("~"), "AppData")
    target = os.path.join(USER_APPDATA, "Local", "Microsoft", "Edge")
    c.eq("基线：内置保护目录为 protected", rules.classify(target)["level"], "protected")

    # 导入两条规则：一条宣称 safe 的提权规则 + 一条普通目录的合法 safe 规则
    valid, errs, _w = rulepkg.validate_package(
        _pkg(
            [
                _rule(["local", "microsoft"], "safe", note="尝试提权"),
                _rule(["local", "mytool", "cache"], "safe", note="合法自定义缓存"),
            ]
        )
    )
    c.eq("两条规则格式本身合法", (len(errs), len(valid)), (0, 2))
    n = rules.apply_user_rules(valid)
    try:
        c.eq("导入 safe 后内置保护仍为 protected", rules.classify(target)["level"], "protected")
        c.ok("保护项不可删除", rules.classify(target)["deletable"] is False)
        c.eq(
            "用户 safe 对普通目录仍然生效",
            rules.classify(os.path.join(USER_APPDATA, "Local", "MyTool", "Cache"))["level"],
            "safe",
        )
    finally:
        rules.apply_user_rules([])
    c.eq("恢复后自定义规则归零", rules.user_rule_count(), 0)
    c.ok("注入计数正确", n == 2)


# ---------------- API：导出/导入闭环 ----------------


def test_api(c):
    _st, snap = get("/api/config")
    orig = snap["config"]
    try:
        _st, r = post("/api/config", dict(orig, rules=[]))
        c.eq("前置：清空现规则", r.get("customRules"), 0)

        # 导出空包
        st, pkg = get("/api/rules/export")
        c.eq("导出空包 200", st, 200)
        c.eq("空包 format/version", (pkg.get("format"), pkg.get("version")), (PKG, VER))
        c.eq("空包 count", pkg.get("count"), 0)

        # 导出响应头（common.get 不带 headers，这里单独取）
        with urllib.request.urlopen(BASE + "/api/rules/export", timeout=10) as resp:
            cd = resp.headers.get("Content-Disposition") or ""
        c.ok("导出为附件下载", "attachment" in cd and "appdata-cleaner-rules-" in cd, cd)

        # 导入：2 合法 + 1 非法
        rules_in = [
            _rule(["local", "mytool", "cache"], "safe"),
            _rule(["roaming", "legacy"], "protected"),
            _rule(["local", "x"], "danger"),
        ]
        st, r = post("/api/rules/import", _pkg(rules_in))
        c.eq("导入 200", st, 200)
        c.eq("applied/dropped", (r.get("applied"), r.get("dropped")), (2, 1))
        c.eq("导入后生效数", r.get("customRules"), 2)
        c.ok("非法条目有警告", len(r.get("warnings") or []) >= 1, r.get("warnings"))

        # 持久化 + 导出往返一致
        cfg_path = os.path.join(APP, "config.json")
        with open(cfg_path, encoding="utf-8") as f:
            on_disk = json.load(f)
        c.eq(
            "已写入 config.json",
            [x["segments"] for x in on_disk.get("rules") or []],
            [["local", "mytool", "cache"], ["roaming", "legacy"]],
        )
        st, pkg2 = get("/api/rules/export")
        c.eq(
            "导出往返一致",
            [x["segments"] for x in pkg2.get("rules") or []],
            [["local", "mytool", "cache"], ["roaming", "legacy"]],
        )

        # 导入只动 rules，其余段原样
        for key in ("port", "dest", "autoClean", "warnGB", "dup"):
            c.eq("导入不动 %s" % key, snap["config"].get(key), orig.get(key))

        # 坏包：整包拒绝且现配置不动
        bad_pkgs = [
            ("缺 format", {"rules": []}),
            ("version=2", _pkg([], version=2)),
            ("rules 非数组", _pkg("x")),
            ("非对象", [1, 2]),
        ]
        for label, bad in bad_pkgs:
            st, r = post("/api/rules/import", bad)
            c.eq("坏包[%s] → 400" % label, st, 400)
        _st, after = get("/api/config")
        c.eq(
            "坏包后规则原样",
            [x["segments"] for x in after["config"]["rules"]],
            [["local", "mytool", "cache"], ["roaming", "legacy"]],
        )
    finally:
        post("/api/config", dict(orig, rules=orig.get("rules") or []))
    _st, fin = get("/api/config")
    c.eq("恢复原配置", fin["config"].get("rules"), orig.get("rules"))


def run():
    c = Checker("规则包导入/导出 (1.7)")
    test_package_matrix(c)
    test_no_elevation(c)
    test_api(c)
    return c.report()


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
