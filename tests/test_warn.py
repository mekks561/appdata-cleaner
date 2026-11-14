# -*- coding: utf-8 -*-
"""1.4 R5：空间预警阈值（config.warnGB）的校验、向前兼容与服务接口贯通。

不改动服务运行中的全局配置：只做纯配置单元 + GET 读取 + 一次「非法值
必须被 400 拒绝」的写入尝试（拒绝路径不会写盘、不会替换 CFG）。
前端横幅的显示逻辑由 logs/ui_check_m3.py 无头核验。
"""

import json
import shutil
import tempfile

from core import config
from tests.common import Checker, get, post


def test_validate(c):
    c.eq("默认阈值 10 GB", config.DEFAULTS["warnGB"], 10)
    for v in (0, 1, 10, 100, 10000):
        cfg, errs, _w = config.validate({"warnGB": v})
        c.eq("合法阈值 %r 通过" % v, cfg["warnGB"], v)
        c.eq("合法阈值 %r 无错误" % v, errs, [])
    for v in (-1, 10001, "10", True, 10.5, None, [], {}):
        cfg, errs, _w = config.validate({"warnGB": v})
        c.ok("非法阈值 %r 被拒" % (v,), any("warnGB" in e for e in errs), errs)
        c.eq("非法阈值 %r 回退默认" % (v,), cfg["warnGB"], 10)
    cfg, errs, _w = config.validate({})
    c.eq("缺字段时用默认值", cfg["warnGB"], 10)
    c.eq("缺字段不产生错误", errs, [])
    # 坏阈值不影响同一次保存里的其他合法字段
    cfg, errs, _w = config.validate({"port": 8901, "warnGB": -1})
    c.eq("其他字段仍被正常校验", cfg["port"], 8901)
    c.ok("错误里点名 warnGB", any("warnGB" in e for e in errs), errs)


def test_compat(c):
    tmp = tempfile.mkdtemp(prefix="awarn-")
    try:
        # 升级前已有的 config.json（没有 warnGB 字段）：启动加载必须补默认值
        p = config.config_path(tmp)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"port": 8765, "dest": r"D:\AppDataBackup", "rules": []}, f)
        cfg, warn = config.load(tmp)
        c.eq("旧配置补默认阈值", cfg["warnGB"], 10)
        c.eq("旧配置原有字段保留", cfg["port"], 8765)
        c.eq("旧配置加载无警告", warn, [])

        # 保存 / 回读闭环
        good, errs, _w = config.validate({"warnGB": 0, "port": 8902})
        c.eq("构造合法配置无错误", errs, [])
        c.eq("0 表示关闭预警", good["warnGB"], 0)
        c.eq("save 无错误", config.save(tmp, good), None)
        cfg2, _w = config.load(tmp)
        c.eq("回读关闭状态", cfg2["warnGB"], 0)
        c.eq("回读端口", cfg2["port"], 8902)

        # 坏值文件（手写）启动加载时整段回退，服务不能起不来
        with open(config.config_path(tmp), "w", encoding="utf-8") as f:
            json.dump({"warnGB": "abc", "port": 8765}, f)
        cfg3, _w = config.load(tmp)
        c.eq("坏阈值文件回退默认", cfg3["warnGB"], 10)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_api(c):
    st, info = get("/api/info")
    c.eq("/api/info 返回 200", st, 200)
    c.ok("/api/info 下发 warnGB", "warnGB" in info, info.get("warnGB"))
    st2, cfg = get("/api/config")
    c.eq("/api/config 返回 200", st2, 200)
    c.eq("接口两处阈值一致", info.get("warnGB"), (cfg.get("config") or {}).get("warnGB"))

    # 非法阈值走保存接口必须 400，且不能改写运行中的配置（拒绝路径不写盘）
    body = dict(cfg.get("config") or {})
    body["warnGB"] = -1
    st3, bad = post("/api/config", body)
    c.eq("非法阈值保存被 400 拒绝", st3, 400)
    c.ok("拒绝原因点名 warnGB",
         any("warnGB" in str(x) for x in (bad.get("details") or [])), bad)
    st4, info2 = get("/api/info")
    c.eq("拒绝后阈值未被改写", info2.get("warnGB"), info.get("warnGB"))


def run():
    c = Checker("空间预警阈值 (1.4 R5)")
    test_validate(c)
    test_compat(c)
    test_api(c)
    return c.report()


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
