# -*- coding: utf-8 -*-
"""1.9 多根目录：校验矩阵、增删改闭环、旧配置兼容、零回归。

分层：
  单元 —— core/roots.py 的路径/标签校验矩阵（不起服务）；
  API  —— 三个端点闭环、持久化、默认根不可删、超限；
  兼容 —— 旧 config 缺 roots、/api/config 不带 roots 不清空、
           /api/scan 传未登记路径仍可用（§2 四条契约）。

API 段会修改 CFG["roots"]：开头整份配置快照，finally 恢复。
本组放在回归序列最后，避免扫描结果影响其他组。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config  # noqa: E402
from core import roots as roots_mod
from tests.common import APP, Checker, get, post  # noqa: E402

HOME = os.path.expanduser("~")
DEFAULT = roots_mod.default_root(HOME)


def _mk(name):
    d = os.path.join(APP, "testdata", "roots_sandbox", name)
    os.makedirs(d, exist_ok=True)
    return d


# ---------------- 单元：校验矩阵 ----------------


def test_unit(c):
    c.eq("None → 空表合法", roots_mod.validate_list(None, HOME), ([], []))
    out, errs = roots_mod.validate_list("oops", HOME)
    c.eq("非数组 → 报错", (out, len(errs)), ([], 1))
    out, errs = roots_mod.validate_list([{"path": "x"}] * 9, HOME)
    c.ok("超 MAX_ROOTS → 报错", bool(errs), errs)

    bad = [
        ("相对路径", "relative/dir"),
        ("盘符根", "C:\\"),
        ("UNC", "\\\\server\\share"),
        ("穿越段", os.path.join(APP, "..", "evil")),
        ("home 外的系统目录", "C:\\Windows\\System32"),
        ("home 自身", HOME),
        ("不存在的目录", os.path.join(APP, "no_such_dir_9f3a")),
        ("系统保护段（home 内）", os.path.join(APP, "windows", "foo")),
        ("超长路径", os.path.join(APP, "x" * 600)),
    ]
    for label, p in bad:
        r, err = roots_mod.validate_item(p, HOME, [DEFAULT])
        c.ok("拒绝：" + label, r is None and bool(err), (p, err))

    good = _mk("Alpha")
    r, err = roots_mod.validate_item(good, HOME, [DEFAULT], " 我的下载 ")
    c.ok("合法根通过", r is not None and err is None, err)
    c.eq("路径已规范化绝对路径", r["path"], os.path.normpath(os.path.abspath(good)))
    c.eq("label 去空白", r["label"], "我的下载")

    r, err = roots_mod.validate_item(good, HOME, [DEFAULT], "a\x01b\x7f" + "x" * 50)
    c.ok("label 去控制字符且限长", r["label"] == "ab" + "x" * 30, r["label"])
    r, err = roots_mod.validate_item(good, HOME, [DEFAULT])
    c.eq("label 缺省取目录名", r["label"], "Alpha")

    # 重复 / 嵌套
    _, err = roots_mod.validate_item(good, HOME, [DEFAULT, good])
    c.ok("重复根被拒", "重复或相互嵌套" in (err or ""), err)
    sub = _mk("Alpha/sub")
    _, err = roots_mod.validate_item(sub, HOME, [DEFAULT, good])
    c.ok("子目录（嵌套）被拒", "重复或相互嵌套" in (err or ""), err)
    _, err = roots_mod.validate_item(DEFAULT, HOME, [DEFAULT])
    c.ok("默认根占位（嵌套）被拒", "重复或相互嵌套" in (err or ""), err)

    # config 层降级语义
    cfg, errors, warns = config.validate(
        {"roots": [{"path": good, "label": "合法"}, {"path": "C:\\", "label": "坏"}], "rules": []}
    )
    c.ok("坏根不产生硬错误", not errors, errors)
    c.eq("合法根保留", len(cfg["roots"]), 1)
    c.ok("坏根降级为警告", any("已忽略" in w for w in warns), warns)

    cfg2, _e, _w = config.validate({})
    c.eq("旧配置缺 roots → 空表", cfg2["roots"], [])


# ---------------- API：端点闭环 ----------------


def test_api(c):
    _st, snap = get("/api/config")
    orig = snap["config"]
    orig_roots = orig.get("roots") or []
    a = _mk("Alpha")
    b = _mk("Beta")
    try:
        st, v = get("/api/roots")
        c.eq("GET /api/roots → 200", st, 200)
        c.ok(
            "默认根恒第一且 default=True",
            v["roots"][0]["path"] == DEFAULT and v["roots"][0]["default"] is True,
            v["roots"][0],
        )
        c.eq("max=8", v["max"], roots_mod.MAX_ROOTS)
        c.eq("defaultRoot 正确", v["defaultRoot"], DEFAULT)
        c.ok("带 userHome/current", bool(v["userHome"]) and bool(v["current"]), v)

        # 清空现注册表，保证用例可重复
        st, r = post("/api/config", dict(orig, roots=[]))
        c.eq("前置：清空根表", st, 200)

        st, r = post("/api/roots/add", {"path": a, "label": "下载目录"})
        c.eq("add 合法 → 200", st, 200)
        c.ok(
            "返回新根列表",
            any(x["path"] == a and x["label"] == "下载目录" for x in r.get("roots", [])),
            r,
        )
        _st, cfg = get("/api/config")
        c.ok("已持久化到 config", any(x["path"] == a for x in cfg["config"]["roots"]), cfg)

        st, r = post("/api/roots/add", {"path": a})
        c.eq("重复 add → 400", st, 400)
        st, r = post("/api/roots/add", {"path": os.path.join(a, "sub")})
        c.eq("嵌套 add → 400", st, 400)
        st, r = post("/api/roots/add", {"path": "C:\\"})
        c.eq("盘符根 add → 400", st, 400)
        st, r = post("/api/roots/add", {"path": os.path.join(APP, "not_exist_77")})
        c.eq("不存在 add → 400", st, 400)
        st, r = post("/api/roots/add", {"path": DEFAULT})
        c.eq("默认根 add → 400（嵌套）", st, 400)

        st, r = post("/api/roots/add", {"path": b, "label": "开发缓存"})
        c.eq("add 第二个 → 200", st, 200)
        st, r = post("/api/roots/remove", {"path": a})
        c.eq("remove → 200", st, 200)
        c.ok("删除后列表不含 a", all(x["path"] != a for x in r["roots"]), r["roots"])
        st, r = post("/api/roots/remove", {"path": DEFAULT})
        c.eq("删默认根 → 400", st, 400)
        st, r = post("/api/roots/remove", {"path": a})
        c.eq("删不存在的根 → 400", st, 400)

        # 超限：补满 8 个再试第 9 个
        for i in range(8):
            p = _mk("Limit%d" % i)
            st, r = post("/api/roots/add", {"path": p})
            if st != 200:
                break
        _st, cur = get("/api/config")
        c.eq("已登记 8 个", len(cur["config"]["roots"]), roots_mod.MAX_ROOTS)
        p9 = _mk("Overflow")
        st, r = post("/api/roots/add", {"path": p9})
        c.eq("第 9 个 → 400", st, 400)

        # 兼容：不带 roots 保存配置不清空根表
        _st, cur = get("/api/config")
        body = {k: v2 for k, v2 in cur["config"].items() if k != "roots"}
        st, r = post("/api/config", body)
        c.eq("旧客户端 POST /api/config → 200", st, 200)
        _st, after = get("/api/config")
        c.eq("根表未被清空", len(after["config"]["roots"]), roots_mod.MAX_ROOTS)

        # 兼容：/api/scan 传「未登记」的 home 内路径仍可扫
        st, r = post("/api/scan", {"root": b, "maxDepth": 2, "hotMB": 1, "bigMB": 5})
        c.eq("未登记根扫描未被拒绝", st, 200, r)
    finally:
        post("/api/config", dict(orig, roots=orig_roots))
        import shutil

        shutil.rmtree(os.path.join(APP, "testdata", "roots_sandbox"), ignore_errors=True)
    _st, fin = get("/api/config")
    c.eq("配置快照还原", fin["config"]["roots"], orig_roots)


def run():
    c = Checker("roots(1.9 多根目录)")
    test_unit(c)
    test_api(c)
    return c.report()


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
