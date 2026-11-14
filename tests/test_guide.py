# -*- coding: utf-8 -*-
"""1.8 解锁引导：protected 复核、内容白名单、路径校验、零执行面。

分层：
  单元 —— guide.build_guide 的路径校验与复核矩阵（不起服务）；
  API  —— POST /api/unlock-guide 端到端：内置保护 / 自定义保护 / 非保护 / 通用；
  静态 —— 引导内容防御断言：链接白名单、免责声明、模块零 IO 面。

只读端点，不改任何配置与状态；API 段会临时注入一条自定义 protected 规则，
finally 恢复原配置。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import guide  # noqa: E402
from tests.common import Checker, post  # noqa: E402

ALLOWED_PREFIXES = ("https://support.microsoft.com/", "https://learn.microsoft.com/")


def _links_ok(links):
    return (
        isinstance(links, list)
        and len(links) >= 3
        and all(
            isinstance(lnk, dict)
            and isinstance(lnk.get("title"), str)
            and isinstance(lnk.get("url"), str)
            and lnk["url"].startswith(ALLOWED_PREFIXES)
            for lnk in links
        )
    )


# ---------------- 单元：路径校验与复核 ----------------


def test_unit(c):
    st, b = guide.build_guide(None)
    c.eq("省略 path → 200 通用引导", st, 200)
    c.ok("通用引导 protected=False", b["protected"] is False, b)
    c.ok("通用引导含步骤与链接", b["steps"] and _links_ok(b["links"]), b)
    c.ok("通用引导含免责声明", "不代为修改" in b["disclaimer"], b)

    st, b = guide.build_guide("")
    c.eq("空字符串 path → 通用引导", (st, b["protected"]), (200, False))

    st, b = guide.build_guide(123)
    c.eq("非字符串 path → 400", st, 400)
    st, b = guide.build_guide("x" * 5000)
    c.eq("超长 path → 400", st, 400)

    st, b = guide.build_guide(r"C:\Users\x\AppData\Local\Microsoft")
    c.eq("内置保护路径 → 200", st, 200)
    c.ok("复核为 protected", b["protected"] is True, b)
    c.eq("命中规则 local/microsoft", b.get("rule"), "local/microsoft")
    c.eq("vendor 是 Microsoft", b.get("vendor"), "Microsoft")
    c.ok("note 来自内置规则", bool(b.get("note")), b)
    c.ok("custom=False", b.get("custom") is False, b)
    c.ok("保护命中同样带步骤与链接", _links_ok(b["links"]), b)

    st, b = guide.build_guide(r"C:\Users\x\AppData\Local\Microsoft\Edge\User Data")
    c.ok("保护目录深层仍命中（前缀树）", b["protected"] is True, b)

    st, b = guide.build_guide(r"C:\Users\x\AppData\Local\Temp")
    c.ok("safe 路径 → 不给 protected", b["protected"] is False, b)
    st, b = guide.build_guide(r"C:\Users\x\Documents\work")
    c.ok("data 路径 → 不给 protected", b["protected"] is False, b)
    st, b = guide.build_guide("not a windows path")
    c.ok("普通字符串未命中 → protected=False", b["protected"] is False, b)


def test_no_io_surface(c):
    """静态断言：guide 模块零执行面（PLAN-1.8 §4 零新增攻击面）。"""
    import inspect
    import re

    src = inspect.getsource(guide)
    c.ok("无 subprocess 调用", "subprocess" not in src)
    c.ok("无 os 命令执行", not re.search(r"os\.(system|popen|remove|rename|rmdir)", src))
    c.ok("无 shutil", "shutil" not in src)
    c.ok("无文件读写", "open(" not in src)
    c.ok("无 stat/磁盘探测", not re.search(r"os\.(stat|lstat|scandir|listdir|walk)", src))
    c.ok("无 eval/exec", not re.search(r"\b(eval|exec|__import__)\s*\(", src))
    c.ok("无 ctypes/winreg", "ctypes" not in src and "winreg" not in src)


# ---------------- API 端到端 ----------------


def test_api(c):
    # 快照整份配置（只发 rules 会把 port/dest 等重置为默认），finally 恢复
    from tests.common import get

    _st, snap = get("/api/config")
    orig = snap["config"]
    orig_rules = orig.get("rules") or []

    try:
        # 注入一条自定义 protected 规则（走与 UI 相同的保存路径）
        user_rule = {
            "segments": ["local", "testguideapp"],
            "level": "protected",
            "note": "测试专用保护目录",
            "vendor": "自定义",
        }
        st, r = post("/api/config", dict(orig, rules=orig_rules + [user_rule]))
        c.ok("注入自定义 protected 规则成功", st == 200, st)

        st, b = post("/api/unlock-guide", {"path": r"C:\Users\x\AppData\Local\TestGuideApp"})
        c.eq("自定义保护路径 → 200", st, 200)
        c.ok("复核为 protected", b.get("protected") is True, b)
        c.ok("note 带（自定义规则）标注", "自定义规则" in (b.get("note") or ""), b)
        c.ok("custom=True", b.get("custom") is True, b)
        c.ok("自定义保护同样带白名单链接", _links_ok(b.get("links") or []), b)

        st, b = post("/api/unlock-guide", {"path": r"C:\Users\x\AppData\Local\Temp"})
        c.ok("safe 路径 API 复核 protected=False", b.get("protected") is False, b)

        st, b = post("/api/unlock-guide", {})
        c.eq("省略 path API → 通用引导", (st, b.get("protected")), (200, False))

        st, b = post("/api/unlock-guide", {"path": 42})
        c.eq("非法 path API → 400", st, 400)

        st, b = post("/api/unlock-guide", {"path": "a" * 5000})
        c.eq("超长 path API → 400", st, 400)
    finally:
        post("/api/config", dict(orig, rules=orig_rules))
    _st, fin = get("/api/config")
    c.eq("配置快照还原", fin.get("config"), orig)


def run():
    c = Checker("guide(1.8 解锁引导)")
    test_unit(c)
    test_no_io_surface(c)
    test_api(c)
    return c.report()


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
