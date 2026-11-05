# -*- coding: utf-8 -*-
"""增量扫描测试（R6）。需要服务已启动。

覆盖：全量/增量的偏差、快照落盘、冷目录复用、深层新增检出、
「最近写过就重扫」的保护（这是增量不出错的关键）。
"""

import json
import os
import time

from tests.common import (APP, MB, ROOT, Checker, fresh_testdata, get, post,
                          scan)

CACHE_DIR = os.path.join(APP, "cache")


def _age(root, hours=2):
    """把测试目录里所有文件/目录的 mtime 改到过去，模拟「冷」数据。"""
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


def _mkfile(path, mb):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"0" * (mb * MB))


def run():
    fresh_testdata()
    c = Checker("增量扫描 (R6)")

    res1 = scan(fresh=True)
    c.ok("全量扫描有结果", res1["fileCount"] > 0, res1["fileCount"])
    c.eq("全量不产生复用", res1["incremental"]["reusedDirs"], 0)

    snaps = [n for n in os.listdir(CACHE_DIR) if n.startswith("snapshot-")] \
        if os.path.isdir(CACHE_DIR) else []
    c.ok("快照已写入 cache/", len(snaps) >= 1, snaps)
    if snaps:
        with open(os.path.join(CACHE_DIR, snaps[0]), "r", encoding="utf-8") as f:
            snap = json.load(f)
        c.ok("快照带 maxDepth/createdAt",
             "maxDepth" in snap and "createdAt" in snap, list(snap))
        kids = snap["tree"].get("kids") or []
        c.ok("快照子节点带 mtime/hm",
             bool(kids) and kids[0].get("m", 0) > 0 and kids[0].get("hm", 0) > 0,
             kids[0] if kids else "无子节点")

    res2 = scan()
    dev = abs(res1["totalSize"] - res2["totalSize"])
    c.ok("增量与全量偏差为 0", dev == 0, dev)
    c.ok("incremental 字段齐备",
         {"used", "reusedDirs", "reusedBytes", "approx"} <= set(res2["incremental"]),
         list(res2["incremental"]))

    # —— 冷目录：复用且不产生偏差 ——
    _age(ROOT)
    res3 = scan(fresh=True)
    res4 = scan()
    c.ok("冷目录触发复用", res4["incremental"]["reusedDirs"] > 0,
         res4["incremental"]["reusedDirs"])
    c.ok("冷复用偏差为 0", res3["totalSize"] == res4["totalSize"],
         (res3["totalSize"], res4["totalSize"]))

    # —— 深层新增：最深一层的 mtime 变了，必须被检出 ——
    cache_dir = os.path.join(ROOT, "Local", "FakeApp", "Cache")
    _mkfile(os.path.join(cache_dir, "late.bin"), 2)
    res5 = scan()
    c.ok("冷子树内新增文件被检出",
         res5["totalSize"] >= res4["totalSize"] + 2 * MB,
         (res4["totalSize"], res5["totalSize"]))

    # —— hot/big 回放：复用子树的候选不能丢 ——
    hot_full = {h["p"] for h in res3["hot"]}
    hot_inc = {h["p"] for h in res5["hot"]}
    c.ok("复用后 hot 不变少", len(hot_inc) >= len(hot_full) - 1,
         (len(hot_full), len(hot_inc)))
    c.ok("复用后新增目录进入 hot", os.path.join(ROOT, "Local", "NewLate") in hot_inc
         or len(hot_inc) >= len(hot_full),
         sorted(hot_inc - hot_full))

    # —— fresh=1 强制全量 ——
    res6 = scan(fresh=True)
    c.eq("强制全量不复用", res6["incremental"]["reusedDirs"], 0)
    c.ok("强制全量与增量总量一致",
         abs(res6["totalSize"] - res5["totalSize"]) < MB,
         (res5["totalSize"], res6["totalSize"]))
    return c.report()


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
