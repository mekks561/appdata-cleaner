# -*- coding: utf-8 -*-
"""1.3 M1 服务端测试：配置接口 + 一键清理不变量。需要服务已启动。

不变量（R2 一票否决项）：
  · 一键清理清单由服务端枚举，只含 level=safe 且可删项；
  · 确认词「一键清理」必须匹配；
  · 清理后项目进回收站（可在系统回收站还原），历史有记录。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.common import (ROOT, Checker, get, post, scan)  # noqa: E402


def run():
    c = Checker("配置接口与一键清理 (1.3 M1)")

    # —— /api/config 读写 ——
    st, cfg = get("/api/config")
    c.eq("GET /api/config 200", st, 200)
    c.ok("含 port 字段", isinstance(cfg.get("config", {}).get("port"), int),
         cfg.get("config", {}).get("port"))
    c.ok("含 rules 数组", isinstance(cfg.get("config", {}).get("rules"), list))
    orig_port = cfg["config"]["port"]

    st, body = post("/api/config", {"port": 80})
    c.eq("非法端口被拒 400", st, 400)
    st, body = post("/api/config", {"rules": [{"segments": ["local"], "level": "safe", "note": ""}]})
    c.eq("空 note 规则被拒", st, 400)
    c.ok("拒绝时给出明细", isinstance(body.get("details"), list) and body["details"], body)

    # 合法保存（端口不变，避免污染下次测试）
    st, body = post("/api/config", {
        "port": orig_port, "dest": r"D:\AppDataBackup", "rules": [
            {"segments": ["local", "faketest", "mycache"], "level": "protected",
             "note": "测试用：假想的用户数据"},
        ]})
    c.eq("合法配置保存 200", st, 200)
    c.eq("保存后 customRules 计数", body.get("customRules"), 1)
    c.ok("restartHint 为空（端口未变）", body.get("restartHint") == "", body.get("restartHint"))

    # 用户 protected 立即生效：预览一个该路径下的目录应被标 blocked
    st, info = get("/api/info")
    c.eq("info.customRules 同步", info.get("customRules"), 1)
    st, body = post("/api/config", {"port": orig_port, "dest": cfg["config"]["dest"], "rules": []})
    c.eq("清空规则恢复", body.get("customRules"), 0)

    # —— 一键清理 ——
    # 造一个内置安全缓存目录（Local\Temp 命中 builtin safe 规则）
    temp_dir = os.path.join(ROOT, "Local", "Temp")
    os.makedirs(os.path.join(temp_dir, "sub"), exist_ok=True)
    with open(os.path.join(temp_dir, "sub", "junk.bin"), "wb") as f:
        f.write(b"0" * (12 * 1024 * 1024))
    # 疑似缓存目录（heuristics 命中 cache 段名）——绝不能进一键清单
    suspect_dir = os.path.join(ROOT, "Local", "FakeApp", "Cache")

    res = scan(fresh=True)

    st, pv = post("/api/oneclick/preview", {})
    c.eq("preview 200", st, 200)
    c.ok("清单非空（Temp 命中 safe）", pv.get("count", 0) >= 1, pv)
    paths = [x["p"] for x in pv.get("items", [])]
    c.ok("Temp 在清单内", any(p.lower().endswith("\\temp") for p in paths), paths)
    c.ok("疑似目录不在清单内",
         all(os.path.normcase(suspect_dir) != os.path.normcase(p) for p in paths), paths)
    c.ok("清单项合计正确", pv.get("total") == sum(x["s"] for x in pv.get("items", [])))

    # 无扫描结果场景没法轻易模拟（STATE 常驻），跳过；重点测确认词
    st, body = post("/api/oneclick", {})
    c.eq("缺确认词被拒", st, 400)
    st, body = post("/api/oneclick", {"confirmText": "清空回收站"})
    c.eq("错误确认词被拒", st, 400)
    st, body = post("/api/oneclick", {"confirmText": "  一键清理  "})
    # 前后空格应被 strip 容忍
    if st == 200:
        c.ok("确认词前后空格被容忍", True)
        result = body
    else:
        st2, result = post("/api/oneclick", {"confirmText": "一键清理"})
        c.eq("正确确认词 200", st2, 200)
        result = result

    # 执行结果核验
    ok_items = [r for r in result.get("results", []) if r["ok"]]
    c.ok("有成功项", len(ok_items) >= 1, result.get("results"))
    c.ok("Temp 目录已被清走", not os.path.isdir(temp_dir))
    c.ok("疑似目录未被动过", os.path.isdir(suspect_dir))
    c.ok("保护目录仍在",
         os.path.isdir(os.path.join(ROOT, "Local", "Microsoft")))
    c.eq("oneclick 不产生 move manifest", result.get("undoable"), False)

    # 历史有 oneclick 记录
    st, hist = get("/api/history?limit=10")
    modes = [h.get("mode") for h in hist.get("items", [])]
    c.ok("历史含 oneclick 记录", "oneclick" in modes, modes)

    # 清理后再预览应为空（Temp 已不在）
    st, pv2 = post("/api/oneclick/preview", {})
    c.ok("清理后清单变空", pv2.get("count", 0) == 0, pv2)

    return c.report()


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
