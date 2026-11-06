# -*- coding: utf-8 -*-
"""设置中心与自定义规则单元测试（1.3 M1：R3）。

不依赖服务：直接调用 core.config 与 core.rules，可单独运行：
    python tests/test_config.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config, rules  # noqa: E402
from tests.common import Checker  # noqa: E402

U = r"C:\Users\Administrator\AppData"


def run():
    c = Checker("设置中心与自定义规则 (1.3 R3)")
    tmp = tempfile.mkdtemp(prefix="acfg-")
    try:
        # —— 默认生成与回读 ——
        cfg, warn = config.load(tmp)
        c.eq("首次加载生成默认配置", cfg["port"], 8765)
        c.ok("默认配置文件已落盘", os.path.isfile(config.config_path(tmp)))
        c.eq("无警告", warn, [])
        cfg2, _ = config.load(tmp)
        c.eq("二次加载与首次一致", cfg2["port"], cfg["port"])

        # —— 写坏文件 → .bad 备份 + 回退默认 ——
        with open(config.config_path(tmp), "w", encoding="utf-8") as f:
            f.write("{ this is not json")
        cfg3, warn3 = config.load(tmp)
        c.eq("坏 JSON 回退默认端口", cfg3["port"], 8765)
        c.ok("坏 JSON 已备份 .bad", os.path.isfile(config.config_path(tmp) + ".bad"))
        c.ok("坏 JSON 有警告", any(".bad" in w for w in warn3), warn3)

        # —— validate：端口 ——
        _, errs, _w = config.validate({"port": 80})
        c.ok("端口 <1024 被拒", any("端口" in e for e in errs), errs)
        _, errs, _w = config.validate({"port": "8765"})
        c.ok("端口字符串被拒", any("端口" in e for e in errs), errs)
        _, errs, _w = config.validate({"port": True})
        c.ok("布尔冒充整数被拒", any("端口" in e for e in errs), errs)

        # —— validate：dest ——
        _, errs, _w = config.validate({"dest": "relative\\path"})
        c.ok("相对路径 dest 被拒", any("绝对路径" in e for e in errs), errs)
        _, errs, _w = config.validate({"dest": "C:\\"})
        c.ok("盘符根 dest 被拒", any("盘符根" in e for e in errs), errs)
        _, errs, _w = config.validate({"dest": os.environ.get("WINDIR", r"C:\Windows") + "\\Temp"})
        c.ok("系统目录内 dest 被拒", any("系统目录" in e for e in errs), errs)
        ok_cfg, errs, _w = config.validate({"dest": r"E:\MyBackup"})
        c.eq("合法 dest 通过", ok_cfg["dest"], os.path.normpath(r"E:\MyBackup"))
        c.eq("合法 dest 无错误", errs, [])

        # —— validate：自定义规则 ——
        _, _cfg, w = (None, None, None)
        good = [{"segments": ["local", "mytool", "cache"], "level": "safe",
                 "note": "自建工具缓存"}]
        cfg_r, errs, w = config.validate({"rules": good})
        c.eq("合法规则通过", len(cfg_r["rules"]), 1)
        c.eq("合法规则无错误", errs, [])
        bad_cases = [
            ({"rules": [{"segments": [], "level": "safe", "note": "x"}]}, "空 segments"),
            ({"rules": [{"segments": ["local"], "level": "danger", "note": "x"}]}, "非法 level"),
            ({"rules": [{"segments": ["local"], "level": "safe", "note": ""}]}, "空 note"),
            ({"rules": [{"segments": ["local", ".."], "level": "safe", "note": "x"}]}, ".. 穿越"),
            ({"rules": [{"segments": ["local\\x"], "level": "safe", "note": "x"}]}, "段内分隔符"),
            ({"rules": [{"segments": ["a"] * 9, "level": "safe", "note": "x"}]}, "超 8 段"),
        ]
        for body, label in bad_cases:
            _c, _e, w = config.validate(body)
            c.ok("规则校验拒绝：%s" % label, len(_e) > 0, _e)
        cfg_many, e_many, w_many = config.validate({"rules": [
            {"segments": ["local", "a"], "level": "safe", "note": "x"},
            {"segments": ["local", "b"], "level": "protected", "note": "y"},
            {"segments": [], "level": "safe", "note": "bad"},
        ]})
        c.eq("部分非法只丢非法条目", len(cfg_many["rules"]), 2)
        c.ok("部分非法给出硬错误（保存时整体拒绝）", len(e_many) == 1, e_many)
        c.eq("部分非法不产生警告", w_many, [])

        # —— 用户规则优先级（防呆不变式） ——
        try:
            # 用户 protected：连内置 safe 都要给它让路
            n = rules.apply_user_rules([
                {"segments": ["local", "temp"], "level": "protected", "note": "测试保护"},
            ])
            c.eq("注入 1 条用户规则", n, 1)
            r1 = rules.classify(U + r"\Local\Temp")
            c.eq("用户 protected 覆盖内置 safe", r1["level"], "protected")
            c.ok("custom 标记", r1.get("custom") is True, r1)
            c.ok("note 带来源标记", "自定义" in r1["note"], r1["note"])

            # 用户 safe 不能覆盖内置 protected
            rules.apply_user_rules([
                {"segments": ["local", "microsoft"], "level": "safe", "note": "想把系统目录标绿"},
            ])
            r2 = rules.classify(U + r"\Local\Microsoft\Edge")
            c.eq("用户 safe 无法覆盖内置 protected", r2["level"], "protected")
            c.ok("未命中用户规则时无 custom 标记", r2.get("custom") is False, r2)

            # 用户 safe 覆盖内置 suspect（对自己的目录有最终解释权）
            rules.apply_user_rules([
                {"segments": ["local", "nvidia"], "level": "safe", "note": "我确认整个 NVIDIA 都是缓存"},
            ])
            r3 = rules.classify(U + r"\Local\NVIDIA")
            c.eq("用户 safe 覆盖内置 suspect", r3["level"], "safe")
            # 但内置 protected 仍然赢
            r4 = rules.classify(U + r"\Local\Microsoft\Windows")
            c.eq("内置 protected 在用户 safe 下仍保护", r4["level"], "protected")
            c.ok("规则计数与注入一致", rules.user_rule_count() >= 1, rules.user_rule_count())
        finally:
            rules.apply_user_rules([])      # 还原全局状态，绝不影响其他测试
        c.eq("清空后规则数归零", rules.user_rule_count(), 0)
        c.eq("还原后 Temp 恢复安全", rules.classify(U + r"\Local\Temp")["level"], "safe")

        # —— save：备份与回读 ——
        cfg_s, _e, _w = config.validate({"port": 8900, "dest": r"E:\B", "rules": good})
        err = config.save(tmp, cfg_s)
        c.eq("save 无错误", err, None)
        c.ok("旧配置备份为 .bak", os.path.isfile(config.config_path(tmp) + ".bak"))
        cfg_l, _w = config.load(tmp)
        c.eq("保存后回读端口", cfg_l["port"], 8900)
        c.eq("保存后回读规则", len(cfg_l["rules"]), 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return c.report()


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
