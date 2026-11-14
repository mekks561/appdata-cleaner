# -*- coding: utf-8 -*-
"""端到端流程：扫描 → 预览 → 回收站 → 移动 → 永久删除 → 只读文件 → 保护目录。

需要服务已启动。每轮都会重建 testdata/，可反复运行。
"""

import os

from tests.common import APP, ROOT, Checker, exec_, fresh_testdata, preview, scan


def run():
    c = Checker("端到端流程")
    fresh_testdata()

    # 1 扫描
    res = scan()
    c.ok("扫描出文件", res["fileCount"] >= 7, res["fileCount"])
    c.ok("总体积 > 40 MB", res["totalSize"] > 40 * 1024 * 1024, res["totalSize"])

    hot = {h["p"]: h for h in res["hot"]}
    cache_dir = os.path.join(ROOT, "Local", "FakeApp", "Cache")
    edge_dir = os.path.join(ROOT, "Local", "MicrosoftEdge", "User Data", "Cache")
    c.ok("Cache 目录进入排行", cache_dir in hot, list(hot)[:5])
    if cache_dir in hot:
        c.eq("普通 Cache 判为 suspect", hot[cache_dir]["level"], "suspect")
    if edge_dir in hot:
        c.eq("MicrosoftEdge 下 Cache 可清理", hot[edge_dir]["level"], "suspect")

    # 2 预览
    pv = preview([cache_dir])
    c.ok("预览体积 > 19 MB", pv["total"] > 19 * 1024 * 1024, pv["total"])
    c.eq("预览未标记 blocked", pv["rows"][0]["blocked"], False)

    # 3 回收站删除
    st, r = exec_(pv["token"], [cache_dir])
    c.eq("回收站执行 200", st, 200)
    c.eq("成功 1 项", r["okCount"], 1)
    c.ok("目录已移入回收站", not os.path.exists(cache_dir))

    # 4 移动（目标在扫描根之外）
    data_dir = os.path.join(ROOT, "Local", "FakeApp", "Data")
    dest = os.path.join(APP, "_moved_test")
    pv = preview([data_dir])
    st, r = exec_(pv["token"], [data_dir], mode="move", dest=dest)
    c.eq("移动成功 1 项", r["okCount"], 1, r["results"])
    c.ok("原路径已消失", not os.path.exists(data_dir))
    c.ok("目标目录已出现", os.path.exists(os.path.join(dest, "Data")), dest)

    # 5 永久删除（带确认词）
    other = os.path.join(ROOT, "Roaming", "OtherApp")
    pv = preview([other])
    st, r = exec_(pv["token"], [other], mode="permanent", confirm="永久删除")
    c.eq("永久删除成功", r["okCount"], 1, r["results"])
    c.ok("永久删除后路径消失", not os.path.exists(other))
    c.ok("永久删除真正释放或持平", r["freeAfter"] >= r["freeBefore"] - 64 * 1024 * 1024)

    # 6 只读文件
    app2 = os.path.join(ROOT, "Local", "App2")
    pv = preview([app2])
    st, r = exec_(pv["token"], [app2], mode="permanent", confirm="永久删除")
    c.eq("只读文件可删除", r["okCount"], 1, r["results"])
    c.ok("只读目录已消失", not os.path.exists(app2))

    # 7 保护目录不可删
    protected = os.path.join(ROOT, "Local", "Microsoft")
    pv = preview([protected])
    c.eq("保护目录 blocked", pv["rows"][0]["blocked"], True)
    st, r = exec_(pv["token"], [protected])
    c.eq("保护目录执行失败", r["failCount"], 1)
    c.ok("保护目录完好", os.path.isdir(protected))

    # 8 清理移动目标
    import shutil
    shutil.rmtree(dest, ignore_errors=True)
    return c.report()


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
