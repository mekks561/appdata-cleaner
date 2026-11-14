# -*- coding: utf-8 -*-
"""规则层单元测试（R1 + R2）：40 例路径分类 + 4 条不变式。

不依赖服务，直接调用 core.rules，可单独运行：
    python tests/test_rules.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import rules  # noqa: E402
from tests.common import Checker  # noqa: E402

U = r"C:\Users\Administrator\AppData"

# (路径, 期望级别)
CASES = [
    # —— protected（10）——
    (U + r"\Local\Microsoft\Edge\User Data", "protected"),
    (U + r"\Local\Microsoft\OneDrive", "protected"),
    (U + r"\Roaming\Microsoft\Windows\Recent", "protected"),
    (U + r"\LocalLow\Microsoft\CryptnetUrlCache", "protected"),
    (U + r"\Local\Programs\Microsoft VS Code", "protected"),
    (U + r"\Local\Packages\Microsoft.WindowsStore_8wekyb3d8bbwe", "protected"),
    (U + r"\Local\ConnectedDevicesPlatform\L.Administrator", "protected"),
    (U + r"\Roaming\Microsoft\Templates", "protected"),
    (U + r"\Local\Microsoft\WindowsApps", "protected"),
    (U + r"\Local\Microsoft\Office\16.0", "protected"),
    # —— safe（10）——
    (U + r"\Local\Temp", "safe"),
    (U + r"\Local\Temp\hsperfdata", "safe"),
    (U + r"\Local\NVIDIA\DXCache", "safe"),
    (U + r"\Local\NVIDIA\GLCache", "safe"),
    (U + r"\Local\NVIDIA\ComputeCache", "safe"),
    (U + r"\Local\AMD\DxCache", "safe"),
    (U + r"\Local\D3DSCache", "safe"),
    (U + r"\Local\CrashDumps", "safe"),
    (U + r"\Local\pip\cache", "safe"),
    (U + r"\Roaming\npm-cache", "safe"),
    # —— suspect（10）——
    (U + r"\Local\MicrosoftEdge\User Data", "data"),  # 1.0 曾被误判为 protected
    (U + r"\Local\Microsoft VS Code\Cache", "suspect"),  # 1.0 曾被误判为 protected
    (U + r"\Local\NVIDIA", "suspect"),  # 1.0 曾整块标为 safe
    (U + r"\Local\AMD", "suspect"),
    (U + r"\Local\Docker\wsl", "suspect"),
    (U + r"\Roaming\Tencent\WXWork", "suspect"),
    (U + r"\Local\Tencent\WeChat", "suspect"),
    (U + r"\Local\Doubao", "suspect"),
    (U + r"\Local\Trae", "suspect"),
    (U + r"\Local\SomeApp\Cache\data", "suspect"),  # 只能靠启发式，不能给 safe
    (U + r"\Local\Teams\Cache", "suspect"),
    # —— data（10）——
    (U + r"\Roaming\SomeApp\data", "data"),
    (U + r"\Local\SomeApp\settings", "data"),
    (U + r"\Local\NVIDIA Corporation\installer", "data"),  # 段不相等，不能命中 nvidia
    (U + r"\Local\Microsoft Corporation\x", "data"),  # 段不相等，不能命中 microsoft
    (U + r"\Local\PackagesX\foo", "data"),  # 段不相等，不能命中 packages
    (U + r"\Roaming\MicrosoftEdge\User Data", "data"),
    (U + r"\Local\SomeApp\logs_backup", "data"),  # 不是 logs 段
    (U + r"\Roaming\Slack\storage", "data"),
    (U + r"\Local\JetBrains\IntelliJIdea2026.1", "data"),
    (U + r"\Local\ProgramsX\foo", "data"),
]


def run():
    c = Checker("规则层 (R1+R2)")
    for path, want in CASES:
        got = rules.classify(path)
        c.ok(
            "%s → %s（实际 %s）" % (path.split("AppData")[-1], want, got["level"]),
            got["level"] == want,
            got["rule"] or got["note"],
        )

    # 不变式 1：protected 一律不可删
    bad = [p for p, want in CASES if want == "protected" and rules.classify(p)["deletable"]]
    c.ok("不变式：protected 全部 deletable=False", not bad, bad)

    # 不变式 2：1.0 的两个误伤样本必须不再是 protected
    for p in (U + r"\Local\MicrosoftEdge\User Data", U + r"\Local\Microsoft VS Code\Cache"):
        c.ok("回归：%s 不再被保护" % p.split("AppData")[-1], not rules.is_protected(p))

    # 不变式 3：厂商根目录不得给 safe
    for p in (U + r"\Local\NVIDIA", U + r"\Local\AMD"):
        c.ok("回归：%s 不是 safe" % p.split("AppData")[-1], rules.classify(p)["level"] != "safe")

    # 不变式 4：safe 必须带说明，用户要知道删了什么
    no_note = [p for p, want in CASES if want == "safe" and not rules.classify(p)["note"]]
    c.ok("不变式：safe 规则均带用途说明", not no_note, no_note)

    return c.report()


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
