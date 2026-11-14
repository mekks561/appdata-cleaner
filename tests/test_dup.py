# -*- coding: utf-8 -*-
"""1.3 M2 重复文件检测测试：判定正确性（单元）+ 接口流程（需服务）。

一票否决项（PLAN-1.3 G1）：
  · 同内容才成组 —— 同大小不同内容必须 0 误报；
  · 硬链接、云盘占位、protected 子树不参与；
  · 清理每组至少保留一份，且只走回收站。
"""

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import dupfind  # noqa: E402
from tests.common import APP, ROOT, Checker, get, post, scan  # noqa: E402

BASE = os.path.join(APP, "_duptest")
KB = 1024
MB = 1024 * 1024


def _write(path, size, fill=b"\0", head=None, tail=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        if head is not None:
            f.write(head)
            f.write(fill * (size - len(head) - len(tail or b"")))
            f.write(tail or b"")
        else:
            left = size
            while left > 0:
                n = min(left, MB)
                f.write(fill * n)
                left -= n


def _touch(path, mtime):
    os.utime(path, (mtime, mtime))


def build_samples():
    """构造 6 类样本，返回关键路径。"""
    if os.path.isdir(BASE):
        shutil.rmtree(BASE, ignore_errors=True)
    big = 300 * KB
    def p(*a):
        return os.path.join(BASE, *a)

    # 1) 同内容不同名（应成组）
    _write(p("Local", "FakeDup", "a1.bin"), big, b"A")
    _write(p("Local", "FakeDup", "sub", "a2.bin"), big, b"A")
    now = time.time()
    _touch(p("Local", "FakeDup", "a1.bin"), now - 86400)   # 旧
    _touch(p("Local", "FakeDup", "sub", "a2.bin"), now)    # 新 → 应被建议保留

    # 2) 同大小不同内容，头尾相同、中段不同（最狠的误报陷阱）
    head, tail = b"H" * (64 * KB), b"T" * (64 * KB)
    _write(p("Local", "FakeDup", "b1.bin"), big, b"X", head, tail)
    _write(p("Local", "FakeDup", "b2.bin"), big, b"Y", head, tail)

    # 3) 三份同内容 → 一组三份
    for i in (1, 2, 3):
        _write(p("Local", "Other", "c%d.bin" % i), 150 * KB, b"C")

    # 4) 硬链接 → 跳过
    _write(p("Local", "FakeDup", "h1.bin"), 200 * KB, b"H")
    try:
        os.link(p("Local", "FakeDup", "h1.bin"), p("Local", "FakeDup", "h2.bin"))
    except OSError:
        pass

    # 5) protected 子树（Local\Packages）→ 整棵不看
    _write(p("Local", "Packages", "AppA", "y1.bin"), 200 * KB, b"P")
    _write(p("Local", "Packages", "AppA", "y2.bin"), 200 * KB, b"P")

    # 6) 小于阈值 → 不参与
    _write(p("Local", "FakeDup", "s1.bin"), 4 * KB, b"S")
    _write(p("Local", "FakeDup", "s2.bin"), 4 * KB, b"S")
    return {
        "root": BASE,
        "a1": p("Local", "FakeDup", "a1.bin"),
        "a2": p("Local", "FakeDup", "sub", "a2.bin"),
        "b1": p("Local", "FakeDup", "b1.bin"),
        "b2": p("Local", "FakeDup", "b2.bin"),
        "c": [p("Local", "Other", "c%d.bin" % i) for i in (1, 2, 3)],
        "h1": p("Local", "FakeDup", "h1.bin"),
        "pkg": p("Local", "Packages", "AppA", "y1.bin"),
        "s1": p("Local", "FakeDup", "s1.bin"),
    }


def _dup_scan(min_mb=0.1, timeout=90):
    """触发 dup 扫描并等到「新结果」（不能只看 running —— 旧结果还在）。"""
    _s, before = get("/api/dup/result")
    before_seq = (before or {}).get("seq")
    st, body = post("/api/dup/scan", {"minSizeMB": min_mb, "fresh": True})
    if st != 200:
        raise RuntimeError("dup 扫描启动失败: %s %s" % (st, body))
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, res = get("/api/dup/result")
        if st == 200 and res.get("seq") is not None and res.get("seq") != before_seq:
            return res
        time.sleep(0.2)
    raise RuntimeError("dup 扫描超时（%ds）" % timeout)


def run():
    c = Checker("重复文件检测 (1.3 M2)")
    s = build_samples()

    # ---------------- 单元：判定正确性 ----------------
    res = dupfind.find_duplicates(s["root"], min_size=100 * KB, budget_sec=30)
    groups = res["groups"]
    all_paths = {cp["p"] for g in groups for cp in g["copies"]}

    c.eq("只成 2 组（同内容 A 组 + 三份 C 组）", len(groups), 2)
    g_a = next((g for g in groups if g["count"] == 2), None)
    c.ok("同内容不同名成组", g_a is not None and
         {cp["p"] for cp in g_a["copies"]} == {s["a1"], s["a2"]},
         [g["count"] for g in groups])
    c.ok("保留建议 = 最新修改的那份", g_a and g_a["keepSuggestion"] == s["a2"],
         g_a and g_a["keepSuggestion"])
    c.ok("保留标记只有一份", g_a and sum(1 for cp in g_a["copies"] if cp["keep"]) == 1)
    c.ok("可回收量 = size × (n-1)",
         g_a and g_a["reclaimable"] == g_a["size"] * (g_a["count"] - 1),
         g_a and (g_a["reclaimable"], g_a["size"]))

    c.ok("头尾相同但中段不同 → 0 误报（硬指标）",
         s["b1"] not in all_paths and s["b2"] not in all_paths, sorted(all_paths))
    c.ok("protected 子树不参与", s["pkg"] not in all_paths, sorted(all_paths))
    c.ok("小于阈值不参与", s["s1"] not in all_paths, sorted(all_paths))
    c.ok("硬链接被计入跳过", res["skipped"]["hardlink"] >= 1, res["skipped"])
    c.ok("三份同内容 → 一组三份",
         any(g["count"] == 3 and {cp["p"] for cp in g["copies"]} == set(s["c"])
             for g in groups), [(g["count"], len(g["copies"])) for g in groups])
    c.ok("skipped 字段齐备",
         {"cloud", "hardlink", "reparse", "denied"} <= set(res["skipped"]), res["skipped"])

    # 预算为 0 → 立刻 partial，不崩溃
    res0 = dupfind.find_duplicates(s["root"], min_size=100 * KB, budget_sec=0)
    c.ok("预算耗尽标 partial 且不崩", res0["partial"] is True, res0["partial"])

    # ---------------- 接口：扫描 → 清理 ----------------
    # 把重复样本搬进扫描根，供服务端扫描
    dup_root = os.path.join(ROOT, "Local", "FakeDup")
    if os.path.isdir(dup_root):
        shutil.rmtree(dup_root, ignore_errors=True)
    shutil.copytree(os.path.join(BASE, "Local", "FakeDup"), dup_root)
    # copytree 不保留硬链接（会变成两份独立文件），删掉副本来保持样本语义
    h2 = os.path.join(dup_root, "h2.bin")
    if os.path.exists(h2):
        os.remove(h2)
    other = os.path.join(ROOT, "Local", "Other")
    if os.path.isdir(other):
        shutil.rmtree(other, ignore_errors=True)
    shutil.copytree(os.path.join(BASE, "Local", "Other"), other)

    scan(fresh=True)
    dres = _dup_scan()
    api_groups = dres.get("groups") or []
    api_paths = {cp["p"] for g in api_groups for cp in g["copies"]}
    c.ok("接口结果含 a1/a2", {os.path.join(dup_root, "a1.bin"),
                            os.path.join(dup_root, "sub", "a2.bin")} <= api_paths,
         sorted(api_paths))
    c.ok("接口结果不含 b1/b2（0 误报）",
         not any(p.lower().endswith("b1.bin") or p.lower().endswith("b2.bin")
                 for p in api_paths), sorted(api_paths))
    c.ok("接口 partial 字段存在", "partial" in dres, list(dres))

    # 清理校验：空组 / 非法策略
    st, _b = post("/api/dup/clean", {"groupHashes": []})
    c.eq("空组被拒 400", st, 400)
    st, _b = post("/api/dup/clean", {"groupHashes": ["deadbeef"], "keepPolicy": "newest"})
    c.eq("未知组被拒 400", st, 400)
    if len(api_groups) >= 1:
        g = api_groups[0]
        st, _b = post("/api/dup/clean", {"groupHashes": [g["hash"]], "keepPolicy": "bogus"})
        c.eq("非法保留策略被拒 400", st, 400)

        st, cr = post("/api/dup/clean", {"groupHashes": [g["hash"]], "keepPolicy": "newest"})
        c.eq("清理 200", st, 200)
        keep = max(g["copies"], key=lambda x: x["m"])["p"]
        c.ok("每组保留一份不动", os.path.exists(keep), keep)
        c.ok("其余进入回收站（已不在原位）",
             all(not os.path.exists(cp["p"]) for cp in g["copies"] if cp["p"] != keep),
             [(cp["p"], os.path.exists(cp["p"])) for cp in g["copies"]])
        c.ok("清理结果有成功项", cr.get("okCount", 0) >= 1, cr.get("results"))
        c.ok("不提供撤销（回收站还原）", cr.get("undoable") is False)
    else:
        c.ok("接口结果至少 1 组", False, "无分组，后续清理项跳过")

    # 清理后再次扫描不应再出现该组
    dres2 = _dup_scan()
    left = {cp["p"] for g in (dres2.get("groups") or []) for cp in g["copies"]}
    if len(api_groups) >= 1:
        cleaned = {cp["p"] for cp in api_groups[0]["copies"]} - left
        c.ok("已清理的项不再出现在重复组里", len(cleaned) >= 1,
             sorted({cp["p"] for cp in api_groups[0]["copies"]} & left))
        c.ok("保留那份仍在", len(cleaned) < len(api_groups[0]["copies"]) or
             api_groups[0]["count"] == len(cleaned),
             (api_groups[0]["count"], len(cleaned)))

    shutil.rmtree(BASE, ignore_errors=True)
    return c.report()


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
