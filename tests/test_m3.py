# -*- coding: utf-8 -*-
"""历史与撤销测试（R9 + R11）。需要服务已启动。

注意：**不测试真正的「清空回收站」**——那会清掉用户回收站里的全部内容，
自动化测试绝不执行。只验证确认词守门。
"""

import os
import shutil

from tests.common import (APP, MB, ROOT, Checker, exec_, fresh_testdata, get,
                          post, preview)


def run():
    fresh_testdata()
    c = Checker("历史与撤销 (R9+R11)")

    data = os.path.join(ROOT, "Local", "FakeApp", "Data")
    dest = os.path.join(APP, "_moved_m3")

    # —— 移动 → manifest ——
    pv = preview([data])
    st, r = exec_(pv["token"], [data], mode="move", dest=dest)
    c.eq("移动成功 1 项", r["okCount"], 1, r["results"])
    mid = r.get("manifestId") or ""
    c.ok("返回 manifestId", bool(mid), mid)
    c.ok("undoable 标记", r["undoable"] is True)
    c.ok("原路径已消失", not os.path.exists(data))
    moved_to = os.path.join(dest, "Data")
    c.ok("目标已出现", os.path.isdir(moved_to), moved_to)

    # —— 历史 ——
    st, h = get("/api/history?limit=20")
    c.eq("history 200", st, 200)
    c.ok("历史含 move 记录",
         any(i.get("mode") == "move" and i.get("manifestId") == mid
             for i in h["items"]),
         [i.get("mode") for i in h["items"]])
    if h["items"]:
        rec = h["items"][0]
        c.ok("记录带时间与计数", bool(rec.get("ts")) and rec.get("count", 0) >= 1, rec.get("ts"))

    # —— 撤销 ——
    st, u = post("/api/undo", {"manifestId": mid})
    c.eq("撤销 200", st, 200)
    c.eq("还原 1 项", u.get("restoredCount"), 1, u.get("results"))
    c.ok("原路径回来了", os.path.isdir(data))
    db = os.path.join(data, "db.sqlite")
    c.ok("还原后内容大小一致", os.path.exists(db) and os.path.getsize(db) == 20 * MB,
         os.path.getsize(db) if os.path.exists(db) else "missing")
    c.ok("备份目录已搬走", not os.path.exists(moved_to))

    st, h2 = get("/api/history?limit=20")
    c.ok("历史含 undo 记录", any(i.get("mode") == "undo" for i in h2["items"]))

    # —— 重复撤销：逐项说明，不崩溃 ——
    st, u2 = post("/api/undo", {"manifestId": mid})
    c.ok("重复撤销返回说明",
         u2.get("results") and "撤销" in u2["results"][0]["msg"],
         u2)

    # —— manifestId 视为不可信输入 ——
    st, u3 = post("/api/undo", {"manifestId": "../../secret.json"})
    c.eq("路径穿越被拒", st, 400)
    st, u4 = post("/api/undo", {"manifestId": "manifest-99999999-000000-ffffff.json"})
    c.eq("不存在的清单 400", st, 400)

    # —— 清空回收站：只测守门，不真清 ——
    st, e1 = post("/api/trash/empty", {"confirmText": "清空"})
    c.eq("确认词不符被拒", st, 400)
    st, e2 = post("/api/trash/empty", {})
    c.eq("缺确认词被拒", st, 400)

    # —— 回收站体积估算接口 ——
    st, ts = get("/api/trash/size")
    c.eq("trash/size 200", st, 200)
    c.ok("trash/size 字段齐备",
         {"ok", "size", "partial"} <= set(ts) or st != 200, ts)

    shutil.rmtree(dest, ignore_errors=True)
    return c.report()


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
