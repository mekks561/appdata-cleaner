# -*- coding: utf-8 -*-
"""1.4 R3：目录变化对比（单元矩阵 + API 闭环）。

语义口径（M1 决策）：对比的是**两棵结果树**，增量复用的子树尺寸与旧树相同，
视为无变化；`approx` 标记本次是否增量，由 UI 披露低估风险。
"""

import os
import shutil

from core import snapshot
from tests.common import APP, ROOT, Checker, fresh_testdata, get, scan

MB = 1024 * 1024
BASE = os.path.join(APP, "diff_fake_root")


def _n(name, size, kids=None, reused=False, level="data"):
    p = os.path.join(BASE, name)
    return {"p": p, "n": name, "s": size, "f": 1, "d": 0,
            "level": level, "deletable": level != "protected",
            "kids": kids if kids is not None else [], "reused": reused}


def _write(path, mb):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"0" * (mb * MB))


def test_units(c):
    old = _n("", 100, [
        _n("A", 10),
        _n("B", 40),
        _n("C", 50, level="protected"),
        _n("E", 5, reused=True),
    ])
    new = _n("", 130, [
        _n("A", 25),
        _n("C", 20, level="protected"),
        _n("D", 35),
        _n("E", 5, reused=True),
    ])
    d = snapshot.compare(old, new, top=20, approx=True, baseline_at="t0")

    c.eq("hasBaseline", d["hasBaseline"], True)
    c.eq("approx 透传", d["approx"], True)
    c.eq("baselineAt 透传", d["baselineAt"], "t0")
    t = d["totals"]
    c.eq("新增计数", t["new"], 1)
    c.eq("移除计数", t["removed"], 1)
    c.eq("增长计数", t["grown"], 1)
    c.eq("缩小计数", t["shrunk"], 1)
    c.eq("净变化取根节点差（不是节点求和）", t["deltaBytes"], 30)
    c.eq("新增项", [x["n"] for x in d["new"]], ["D"])
    c.eq("移除项", [x["n"] for x in d["removed"]], ["B"])
    c.eq("增长 delta", (d["grown"][0]["n"], d["grown"][0]["delta"],
                         d["grown"][0]["before"]), ("A", 15, 10))
    c.eq("缩小 delta", (d["shrunk"][0]["n"], d["shrunk"][0]["delta"],
                         d["shrunk"][0]["before"]), ("C", -30, 50))
    c.ok("移除项带负 delta", d["removed"][0]["delta"] == -40)
    c.ok("根节点不进任何列表",
         all(x["p"] != BASE for group in ("new", "removed", "grown", "shrunk")
             for x in d[group]))
    c.ok("复用节点(delta=0)不进增长/缩小",
         all(x["n"] != "E" for x in d["grown"] + d["shrunk"]))
    c.eq("reusedCount", d["reusedCount"], 1)
    c.eq("分类字段保留", d["shrunk"][0]["level"], "protected")
    c.eq("deletable 字段保留", d["shrunk"][0]["deletable"], False)

    cut = snapshot.compare(old, new, top=1)
    c.eq("top 截断列表长度", len(cut["new"]), 1)
    c.eq("top 截断不影响 totals", cut["totals"]["new"], 1)

    # 路径键规范化：大小写/分隔符不同必须是同一节点，不能误报「一删一增」
    a = _n("", 10, [_n("Sub", 10)])
    b = _n("", 10, [])
    b["kids"] = [dict(_n("Sub", 10), p=os.path.join(BASE, "sub").swapcase()
                      .replace("\\", "/"))]
    d2 = snapshot.compare(a, b)
    c.eq("大小写/分隔符规范化后无新增移除",
         (d2["totals"]["new"], d2["totals"]["removed"]), (0, 0))

    c.eq("旧树为空不崩", snapshot.compare(None, new)["totals"]["new"], 4)
    empty = {"p": BASE, "n": "", "s": 0, "f": 0, "d": 0, "kids": None}
    c.eq("kids=None 视为空", snapshot.compare(empty, new)["hasBaseline"], True)


def test_api(c):
    cache = os.path.join(APP, "cache")
    snapshot.drop(cache, ROOT)          # 清掉前面测试留下的基线
    fresh_testdata()

    first = scan(fresh=True)
    c.ok("首次扫描结果无 diff", first.get("diff") is None, first.get("diff"))
    st, d1 = get("/api/diff")
    c.eq("/api/diff 首次 200", st, 200)
    c.eq("无基线标记", d1.get("hasBaseline"), False)

    second = scan(fresh=True)
    c.ok("第二次结果带基线", bool((second.get("diff") or {}).get("hasBaseline")))
    st, d2 = get("/api/diff")
    c.ok("接口返回基线", bool(d2.get("hasBaseline")))
    c.eq("未改动时四类计数为 0",
         (d2["totals"]["new"], d2["totals"]["removed"],
          d2["totals"]["grown"], d2["totals"]["shrunk"]), (0, 0, 0, 0))
    c.eq("全量扫描 approx=False", d2.get("approx"), False)

    # 制造四类变化：新增目录 / 删除目录 / 目录内增大 / 目录内缩小
    _write(os.path.join(ROOT, "Local", "NewApp", "x.bin"), 10)
    shutil.rmtree(os.path.join(ROOT, "Roaming", "OtherApp"), ignore_errors=True)
    _write(os.path.join(ROOT, "Local", "FakeApp", "Cache", "plus.bin"), 5)
    ro = os.path.join(ROOT, "Local", "App2", "readonly.txt")
    try:
        os.chmod(ro, 0o666)
        os.remove(ro)
    except OSError:
        pass

    third = scan(fresh=True)
    st, d3 = get("/api/diff")
    c.ok("新增目录被检出", any("NewApp" in x["p"] for x in d3.get("new", [])), d3.get("new"))
    c.ok("已移除被检出", any("OtherApp" in x["p"] for x in d3.get("removed", [])),
         d3.get("removed"))
    c.ok("增长被检出", any("FakeApp" in x["p"] for x in d3.get("grown", [])),
         d3.get("grown"))
    c.ok("缩小被检出", any("App2" in x["p"] for x in d3.get("shrunk", [])),
         d3.get("shrunk"))
    c.eq("净变化为正", d3["totals"]["deltaBytes"] > 0, True, d3["totals"])
    c.eq("/api/result 的 diff 与 /api/diff 一致", third.get("diff"), d3)
    c.ok("列表条目带级别", bool(d3["new"][0].get("level")) if d3.get("new") else False)


def run():
    c = Checker("目录变化对比 (1.4 R3)")
    try:
        test_units(c)
        test_api(c)
    finally:
        # 测试卫生：本组会改写 testdata 并留下「改动后」的快照。若不清理，
        # 下一轮 run_tests.py 的增量组会读到这份脏基线而误判复用（已实测踩到）。
        snapshot.drop(os.path.join(APP, "cache"), ROOT)
        fresh_testdata()
    return c.report()


if __name__ == "__main__":
    import sys
    sys.exit(0 if run() else 1)
