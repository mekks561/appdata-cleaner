# -*- coding: utf-8 -*-
"""1.4 R2：并行分片一致性 + APP_SCAN_WORKERS 单线程回退。

核心不变量：**并行与串行的输出结构与字段 100% 一致**。
只比业务字段，刻意排除 scannedAt / elapsed / disk.free 这类必然不同的元信息。
"""

import os
import shutil

from core import scan
from tests.common import APP, Checker

MB = 1024 * 1024


def _build(root):
    files = [
        ("Local/AppA/Cache/a.bin", 12),
        ("Local/AppA/Data/db.sqlite", 20),
        ("Local/AppB/big.bin", 30),
        ("Local/AppB/Sub/deep.bin", 8),
        ("Roaming/Other/settings.json", 1),
        ("Local/Temp/t1.bin", 6),
        ("Local/Microsoft/system.bin", 2),
    ]
    for rel, mb in files:
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(b"0" * (mb * MB))


def _run(root, workers, tag):
    old = os.environ.get("APP_SCAN_WORKERS")
    os.environ["APP_SCAN_WORKERS"] = str(workers)
    cache = os.path.join(APP, "testdata_cache_" + tag)
    shutil.rmtree(cache, ignore_errors=True)
    try:
        return scan.run_scan(root, 3, 1, 5, fresh=True, cache_dir=cache)
    finally:
        if old is None:
            os.environ.pop("APP_SCAN_WORKERS", None)
        else:
            os.environ["APP_SCAN_WORKERS"] = old


def _node_shape(n):
    if not n:
        return None
    kids = tuple(sorted((_node_shape(k) for k in (n.get("kids") or []) if k),
                        key=lambda x: x[0]))
    return (n["p"], n["s"], n["f"], n["d"], n["level"],
            bool(n["deletable"]), kids)


def _shape(res):
    return {
        "totalSize": res["totalSize"],
        "fileCount": res["fileCount"],
        "tree": _node_shape(res["tree"]),
        "hot": sorted((h["p"], h["s"]) for h in res["hot"]),
        "big": sorted((b["p"], b["s"]) for b in res["big"]),
        "errorCount": res["errorCount"],
        "truncated": res["truncated"],
        "incrementalUsed": res["incremental"]["used"],
    }


def test_workers(c):
    old = os.environ.pop("APP_SCAN_WORKERS", None)
    try:
        c.eq("默认 worker 数", scan.scan_workers(), scan.DEFAULT_WORKERS)

        def with_env(v):
            os.environ["APP_SCAN_WORKERS"] = v
            return scan.scan_workers()

        c.eq("=1 走单线程回退", with_env("1"), 1)
        c.eq("=4 生效", with_env("4"), 4)
        c.eq("非法值回退默认", with_env("abc"), scan.DEFAULT_WORKERS)
        c.eq("0 夹到 1", with_env("0"), 1)
        c.eq("99 夹到 16", with_env("99"), 16)
    finally:
        if old is not None:
            os.environ["APP_SCAN_WORKERS"] = old
        else:
            os.environ.pop("APP_SCAN_WORKERS", None)


def test_consistency(c):
    root = os.path.join(APP, "testdata_parallel")
    caches = [os.path.join(APP, "testdata_cache_" + t)
              for t in ("serial", "par8", "par4")]
    try:
        shutil.rmtree(root, ignore_errors=True)
        _build(root)
        serial = _run(root, 1, "serial")
        par8 = _run(root, 8, "par8")
        par4 = _run(root, 4, "par4")

        a, b, d = _shape(serial), _shape(par8), _shape(par4)
        for field in ("totalSize", "fileCount", "tree", "hot", "big",
                      "errorCount", "truncated", "incrementalUsed"):
            c.eq("并行(8) 与串行一致 · " + field, b[field], a[field])
            c.eq("并行(4) 与串行一致 · " + field, d[field], a[field])

        c.ok("确实统计到文件", a["fileCount"] >= 7, a["fileCount"])
        c.ok("深层文件体积计入", a["totalSize"] >= 79 * MB, a["totalSize"])
        c.eq("输出字段集合不变", set(par8.keys()), set(serial.keys()))
        c.ok("scannedAtEpoch 保留", bool(par8.get("scannedAtEpoch")))
        c.ok("首次扫描无 diff 基准", par8.get("diff") is None)

        c.ok("树结构可展开（未因并行丢节点）",
             len(par8["tree"]["kids"] or []) >= 2, len(par8["tree"]["kids"] or []))
    finally:
        shutil.rmtree(root, ignore_errors=True)
        for p in caches:
            shutil.rmtree(p, ignore_errors=True)


def test_diff_baseline_survives(c):
    """并行扫描下，第二次扫描仍能拿到上一次的 diff 基准。"""
    root = os.path.join(APP, "testdata_parallel_diff")
    cache = os.path.join(APP, "testdata_cache_pdiff")
    old = os.environ.get("APP_SCAN_WORKERS")
    try:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(cache, ignore_errors=True)
        _build(root)
        os.environ["APP_SCAN_WORKERS"] = "8"
        first = scan.run_scan(root, 3, 1, 5, fresh=True, cache_dir=cache)
        c.ok("第一次无基线", first.get("diff") is None)
        second = scan.run_scan(root, 3, 1, 5, cache_dir=cache)
        c.ok("第二次拿到基线", bool((second.get("diff") or {}).get("hasBaseline")))
        c.eq("基线时间非空", bool(second["diff"].get("baselineAt")), True)
        c.eq("无变化时新增/移除为 0",
             (second["diff"]["totals"]["new"], second["diff"]["totals"]["removed"]),
             (0, 0))
        # fresh 强制全量也要保留对比基准（不是只有增量才有 diff）
        third = scan.run_scan(root, 3, 1, 5, fresh=True, cache_dir=cache)
        c.ok("强制全量仍有 diff 基准", bool((third.get("diff") or {}).get("hasBaseline")))
        c.eq("全量扫描 approx=False", third["diff"].get("approx"), False)
    finally:
        if old is None:
            os.environ.pop("APP_SCAN_WORKERS", None)
        else:
            os.environ["APP_SCAN_WORKERS"] = old
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(cache, ignore_errors=True)


def run():
    c = Checker("并行扫描一致性 (1.4 R2)")
    test_workers(c)
    test_consistency(c)
    test_diff_baseline_survives(c)
    return c.report()


if __name__ == "__main__":
    import sys
    sys.exit(0 if run() else 1)
