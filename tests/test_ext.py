# -*- coding: utf-8 -*-
"""1.4 R4：全树扩展名聚合。

覆盖：聚合口径（总量/文件数/占比/排序/Top 上限/目录分布）、后缀大小写与
无后缀归一、深层文件的可见目录归属、增量沿用旧统计并标 approx、强制全量
刷新，以及 /api/ext 接口闭环。

与 test_parallel / test_diff 放在最后一组：会重建 testdata 并清快照。
"""

import os
import shutil
import tempfile
import time

from core import scan as scan_mod
from core import snapshot
from tests.common import (APP, MB, ROOT, Checker, fresh_testdata, get, scan)

CACHE_DIR = os.path.join(APP, "cache")


def _build(root):
    files = [
        ("Local/AppA/Cache/a.BIN", 12),          # 后缀大小写归一
        ("Local/AppA/Cache/b.bin", 8),
        ("Local/AppA/Data/db.sqlite", 20),
        ("Local/AppB/Deep/Deeper/deep.bin", 6),  # 超出 max_depth，归属到可见祖先
        ("Roaming/Other/settings.json", 1),
        ("Roaming/Other/Local State", 1),        # 无后缀
        ("Local/Temp/t1.log", 2),
    ]
    for rel, mb in files:
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(b"0" * (mb * MB))
    return root


def _age(root, hours=2):
    """把整棵测试树的 mtime 调到过去，满足 COLD_SECONDS 的复用条件。"""
    old = time.time() - hours * 3600
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            try:
                os.utime(os.path.join(dp, f), (old, old))
            except OSError:
                pass
    for dp, _dn, _fn in os.walk(root, topdown=False):
        try:
            os.utime(dp, (old, old))
        except OSError:
            pass


def test_units(c):
    # —— 空数据 ——
    e = scan_mod.ext_summary({})
    c.eq("空聚合类型数", e["types"], 0)
    c.eq("空聚合总量", e["totalBytes"], 0)
    c.eq("空聚合明细", e["items"], [])
    c.eq("空聚合 approx", e["approx"], False)
    c.eq("空聚合 partial", e["partial"], False)
    c.eq("partial 标记透传", scan_mod.ext_summary({}, partial=True)["partial"], True)

    # —— 总量 / 文件数 / 占比 / 排序 / 目录分布 ——
    m = {
        ".bin": {"s": 100, "f": 2, "dirs": {r"C:\A": [60, 1], r"C:\B": [40, 1]}},
        ".txt": {"s": 20, "f": 1, "dirs": {r"C:\A": [20, 1]}},
        "(无后缀)": {"s": 5, "f": 1, "dirs": {r"C:\C": [5, 1]}},
    }
    e = scan_mod.ext_summary(m, approx=True)
    c.eq("总量按全部类型求和", e["totalBytes"], 125)
    c.eq("文件数按全部类型求和", e["totalFiles"], 4)
    c.eq("类型数", e["types"], 3)
    c.eq("approx 标记透传", e["approx"], True)
    c.eq("体积降序第一", e["items"][0]["ext"], ".bin")
    c.eq("占比以全树总量为分母", e["items"][0]["pct"], 80.0)
    c.eq("目录分布按体积降序", e["items"][0]["dirs"][0]["p"], r"C:\A")
    c.eq("目录分布条目数", len(e["items"][0]["dirs"]), 2)
    c.eq("未截断时 otherDirs 为 0", e["items"][0]["otherDirs"], 0)

    # —— 目录分布 Top N 与 otherDirs 披露 ——
    many = {".dll": {"s": 0, "f": 0, "dirs": {r"C:\d%02d" % i: [10 - i, 1]
                                              for i in range(12)}}}
    many[".dll"]["s"] = sum(v[0] for v in many[".dll"]["dirs"].values())
    many[".dll"]["f"] = 12
    g = scan_mod.ext_summary(many)["items"][0]
    c.eq("目录分布裁剪到上限", len(g["dirs"]), scan_mod.EXT_DIR_SHOW)
    c.eq("其余目录数被披露", g["otherDirs"], 12 - scan_mod.EXT_DIR_SHOW)
    c.ok("裁剪后总量仍为全量", g["s"] == many[".dll"]["s"], g["s"])

    # —— 类型 Top N：只裁输出，totalBytes/types 仍是全量 ——
    wide = {}
    for i in range(scan_mod.EXT_SHOW + 7):
        wide[".e%02d" % i] = {"s": 100 - i, "f": 1, "dirs": {r"C:\X": [100 - i, 1]}}
    w = scan_mod.ext_summary(wide)
    c.eq("输出裁剪到 EXT_SHOW", w["shown"], scan_mod.EXT_SHOW)
    c.eq("types 仍是全量", w["types"], scan_mod.EXT_SHOW + 7)
    c.eq("totalBytes 仍是全量", w["totalBytes"], sum(x["s"] for x in wide.values()))
    c.ok("占比之和小于 100（分母是全量而非前 N 名）",
         sum(x["pct"] for x in w["items"]) < 100)

    # —— ScanCtx.add_ext 按目录批量合并 ——
    ctx = scan_mod.ScanCtx(ROOT, 3, 1, 1)
    ctx.add_ext({".bin": [10, 2]}, r"C:\A")
    ctx.add_ext({".bin": [5, 1], ".txt": [3, 1]}, r"C:\A")
    ctx.add_ext({".bin": [7, 1]}, r"C:\B")
    c.eq("同类型多目录累计体积", ctx.ext[".bin"]["s"], 22)
    c.eq("同类型多目录累计文件数", ctx.ext[".bin"]["f"], 4)
    c.eq("同目录二次提交累加", ctx.ext[".bin"]["dirs"][r"C:\A"], [15, 3])
    c.eq("不同目录分开记", len(ctx.ext[".bin"]["dirs"]), 2)
    s = scan_mod.ext_summary(ctx.ext)
    c.eq("ctx 汇总文件数", s["totalFiles"], 5)
    c.eq("无后缀常量与前端一致", scan_mod.NO_EXT, "(无后缀)")


def test_offline_scan(c):
    root = tempfile.mkdtemp(prefix="adc-exttree-")
    cache = tempfile.mkdtemp(prefix="adc-extcache-")
    try:
        _build(root)
        _age(root)
        full = scan_mod.run_scan(root, 3, 1, 5, fresh=True, cache_dir=cache)
        e = full["ext"]
        c.eq("全量聚合总量等于扫描树体积", e["totalBytes"], full["totalSize"],
             "%d vs %d" % (e["totalBytes"], full["totalSize"]))
        c.eq("全量聚合文件数等于扫描文件数", e["totalFiles"], full["fileCount"])
        c.eq("全量不标 approx", e["approx"], False)
        c.eq("全量不标 partial", e["partial"], False)
        names = {x["ext"] for x in e["items"]}
        c.ok("后缀大小写归一为 .bin", ".bin" in names and ".BIN" not in names, names)
        c.ok("无后缀独立成组", "(无后缀)" in names, names)
        c.ok("多类型被聚合", len(names) >= 5, names)
        c.eq("占比合计接近 100%",
             round(sum(x["pct"] for x in e["items"])), 100,
             sum(x["pct"] for x in e["items"]))

        # 深层文件归属到「结果树最深层可见目录」：deep.bin 在 Deeper（depth4），
        # 应归到 Local\AppB\Deep（depth3），而不是 Deeper 本身。
        bins = next(x for x in e["items"] if x["ext"] == ".bin")
        owners = [d["p"] for d in bins["dirs"]]
        c.ok("深层文件并入可见祖先目录",
             any(p.endswith("Deep") for p in owners) and
             not any(p.endswith("Deeper") for p in owners), owners)

        # 增量复用：复用的子树本轮没读文件，沿用快照里的整份统计并标 approx
        incr = scan_mod.run_scan(root, 3, 1, 5, fresh=False, cache_dir=cache)
        c.ok("冷目录发生增量复用", incr["incremental"]["used"],
             incr["incremental"])
        c.eq("增量轮次标 approx", incr["ext"]["approx"], True)
        c.eq("增量沿用旧统计不标 partial", incr["ext"]["partial"], False)
        c.eq("增量沿用旧统计总量", incr["ext"]["totalBytes"], e["totalBytes"])
        c.eq("增量沿用旧统计类型数", incr["ext"]["types"], e["types"])

        # 强制全量后重新统计，近似标记消除
        fresh2 = scan_mod.run_scan(root, 3, 1, 5, fresh=True, cache_dir=cache)
        c.eq("强制全量后 approx 消除", fresh2["ext"]["approx"], False)
        c.eq("强制全量后总量不变", fresh2["ext"]["totalBytes"], e["totalBytes"])

        # 旧快照（升级前的格式）没有 ext 字段：不能因此带崩扫描
        snapshot.save(cache, root, full["tree"], list(full["hot"]),
                      list(full["big"]), 3, full["elapsed"])
        legacy = scan_mod.run_scan(root, 3, 1, 5, fresh=False, cache_dir=cache)
        c.ok("旧格式快照仍能产出 ext", isinstance(legacy.get("ext"), dict))
        c.ok("旧格式快照按本轮实扫统计", legacy["ext"]["totalBytes"] >= 0)
        if legacy["incremental"]["used"]:
            c.eq("旧格式快照 + 增量标 partial", legacy["ext"]["partial"], True)
            c.eq("旧格式快照 + 增量标 approx", legacy["ext"]["approx"], True)

        # 快照顶层保留 ext（降级裁剪只作用于 tree）
        snap = snapshot.load(cache, root)
        c.ok("快照顶层保存 ext", isinstance((snap or {}).get("ext"), dict))
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(cache, ignore_errors=True)


def test_api(c):
    fresh_testdata()
    snapshot.drop(CACHE_DIR, ROOT)
    res = scan(fresh=True)
    e = res.get("ext")
    c.ok("/api/result 携带 ext", isinstance(e, dict), type(e))
    if not e:
        return
    c.eq("接口全量不标 approx", e["approx"], False)
    c.eq("接口全量不标 partial", e["partial"], False)
    c.eq("接口聚合总量等于树体积", e["totalBytes"], res["totalSize"])
    c.eq("接口聚合文件数等于扫描文件数", e["totalFiles"], res["fileCount"])
    st, ext = get("/api/ext")
    c.eq("/api/ext 返回 200", st, 200)
    c.eq("/api/ext 与 result.ext 类型数一致", ext.get("types"), e["types"])
    c.eq("/api/ext 与 result.ext 总量一致", ext.get("totalBytes"), e["totalBytes"])
    c.eq("/api/ext 明细条数一致", len(ext.get("items") or []), len(e["items"]))
    names = {x["ext"] for x in e["items"]}
    c.ok("接口结果含 .bin", ".bin" in names, names)
    c.ok("接口结果含无后缀项", "(无后缀)" in names, names)
    st2, again = get("/api/result")
    c.eq("/api/result 二次读取 ext 稳定", again.get("ext", {}).get("totalBytes"),
         e["totalBytes"])


def run():
    # 清掉前面组留下的 testdata 快照，保证本组从全量开始且可重复
    snapshot.drop(CACHE_DIR, ROOT)
    c = Checker("全树扩展名聚合 (1.4 R4)")
    test_units(c)
    test_offline_scan(c)
    test_api(c)
    return c.report()


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
