# -*- coding: utf-8 -*-
"""把 run_tests.py 的断言组接入 pytest：一个组 = 一个 test case。

顺序即依赖：diff/ext 会改写 testdata，cli 需要最后延迟 import server，
roots 会触发一次扫描。新增测试组时同时改 run_tests.py 与这里，
test_group_order_matches_run_tests 会兜底检查两边是否一致。
"""

import pathlib

import pytest

from tests import (
    test_api,
    test_auto,
    test_cli,
    test_config,
    test_contract,
    test_diff,
    test_dup,
    test_ext,
    test_flow,
    test_gate,
    test_gui,
    test_guide,
    test_increment,
    test_m1,
    test_m3,
    test_parallel,
    test_release_gate,
    test_roots,
    test_rulepkg,
    test_rules,
    test_warn,
)

GROUPS = [
    test_rules,
    test_rulepkg,
    test_guide,
    test_config,
    test_api,
    test_contract,
    test_flow,
    test_increment,
    test_m3,
    test_m1,
    test_auto,
    test_dup,
    test_warn,
    test_gate,
    test_parallel,
    test_diff,
    test_ext,
    test_cli,
    test_gui,
    test_roots,
    test_release_gate,
]

GROUP_NAMES = [m.__name__.split(".")[-1] for m in GROUPS]


@pytest.mark.usefixtures("server")
@pytest.mark.parametrize("module", GROUPS, ids=GROUP_NAMES)
def test_group(module):
    assert module.run() is True


def test_group_order_matches_run_tests():
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("run_tests.py")
    text = src.read_text(encoding="utf-8")
    missing = [n for n in GROUP_NAMES if ("%s.run()" % n) not in text]
    assert not missing, "run_tests.py 未注册这些组：%s" % missing
