# -*- coding: utf-8 -*-
"""接口与安全测试（R3 安全加固 / R4 版本 / R5 截断）。需要服务已启动。"""

import os
import re

from tests.common import (APP, ROOT, Checker, exec_, fresh_testdata, get, post,
                          preview, scan)


def run():
    fresh_testdata()
    c = Checker("接口与安全 (R3+R4+R5)")

    # —— R4 版本贯通 ——
    st, info = get("/api/info")
    c.eq("GET /api/info 200", st, 200)
    # 版本断言不再硬编码字面量：每次 bump 版本号都要改测试本身就是缺陷。
    # 改为与 server.py 的 APP_VERSION 常量比对，同时校验 x.y.z 格式。
    with open(os.path.join(APP, "server.py"), encoding="utf-8") as f:
        declared = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', f.read()).group(1)
    c.eq("version 与服务端常量一致", info.get("version"), declared)
    c.ok("version 格式为 x.y.z", bool(re.fullmatch(r"\d+\.\d+\.\d+", declared)),
         declared)
    c.ok("build 非空", bool(info.get("build")), info.get("build"))
    c.ok("python 字段存在", bool(info.get("python")), info.get("python"))
    c.ok("logDir 字段存在", bool(info.get("logDir")), info.get("logDir"))
    c.ok("levels 字段含四级",
         set(info.get("levels", {})) == {"safe", "suspect", "data", "protected"},
         info.get("levels"))

    # —— R3 安全 ——
    st, body = post("/api/scan", {"root": ROOT}, {"Origin": "http://evil.test"})
    c.eq("跨站 Origin 被拒", st, 403)
    st, body = post("/api/scan", {"root": ROOT}, {"Host": "evil.test:8765"})
    c.eq("非法 Host 被拒", st, 403)
    st, body = post("/api/preview", raw=b'{"paths":["' + b"x" * 1100000 + b'"]}')
    c.eq("超大请求体被拒", st, 413)

    pv = preview([r"C:\Windows\System32"])
    c.ok("越界路径标记为 blocked", pv["rows"][0]["blocked"] is True)
    c.ok("越界提示写明原因", "扫描目录" in pv["rows"][0]["msg"], pv["rows"][0]["msg"])

    protected = os.path.join(ROOT, "Local", "Microsoft")
    pv2 = preview([protected])
    c.ok("保护目录 blocked", pv2["rows"][0]["blocked"] is True)
    st, ex = exec_(pv2["token"], [protected])
    c.ok("保护目录执行时被拒", ex["results"][0]["ok"] is False, ex["results"][0]["msg"])
    c.ok("保护目录仍然存在", os.path.isdir(protected))

    # —— 令牌一次性 ——
    data_dir = os.path.join(ROOT, "Local", "FakeApp", "Data")
    pv3 = preview([data_dir])
    c.ok("令牌长度 ≥ 30（secrets 生成）", len(pv3["token"]) >= 30, len(pv3["token"]))
    st, first = exec_(pv3["token"], [data_dir], mode="move", dest=r"C:\Windows\Temp")
    c.ok("dest 指向系统目录被拒", first["results"][0]["ok"] is False, first["results"][0]["msg"])
    st, second = exec_(pv3["token"], [data_dir], mode="move", dest=r"D:\Backup")
    c.eq("令牌二次使用被拒", st, 400)
    c.ok("二次使用提示「失效」", "失效" in second.get("error", ""), second)

    # —— 永久删除必须带确认词 ——
    pv4 = preview([data_dir])
    st, body = exec_(pv4["token"], [data_dir], mode="permanent")
    c.eq("缺确认词被拒", st, 400)
    c.ok("目录未被删除", os.path.isdir(data_dir))

    # —— R5 扫描与截断字段 ——
    res = scan()
    tr = res.get("truncated") or {}
    for key in ("hot", "big", "errors"):
        c.ok("truncated.%s 字段齐备" % key,
             isinstance(tr.get(key), dict) and {"shown", "total", "truncated"} <= set(tr[key]),
             tr.get(key))
    c.ok("hot 排行带 level/note", "level" in res["hot"][0] and "note" in res["hot"][0])

    st, lst = post("/api/list", {"path": os.path.join(ROOT, "Local")})
    c.eq("list 200", st, 200)
    for key in ("items", "total", "returned", "partial", "costMs"):
        c.ok("list 含字段 %s" % key, key in lst, list(lst))

    return c.report()


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
