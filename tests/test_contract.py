# -*- coding: utf-8 -*-
"""接口契约：畸形输入不得让服务端断连，必须返回 JSON 对象。

只覆盖「参数校验路径」可安全触发的端点。会真正删除数据、扫描真实用户目录
或打开外部程序的端点（trash/empty、oneclick、dup/scan、open、openrecyclebin、
restart-admin）刻意排除，避免测试产生副作用或拖慢回归。
"""

import json

from .common import ROOT, Checker, post

# 非对象 JSON / 非 JSON / 空 body：服务端应按空对象处理并走字段校验
NON_OBJECT_BODIES = [
    ("空 body", b""),
    ("非 JSON", b"not-json"),
    ("JSON 数组", b"[]"),
    ("JSON null", b"null"),
    ("JSON 字符串", b'"hello"'),
]

# 没有字段时必须拒绝、且绝不能断连的端点（均不触达真实用户目录）
SAFE_ENDPOINTS = [
    "/api/list",
    "/api/preview",
    "/api/exec",
    "/api/undo",
    "/api/rules/import",
    "/api/dup/clean",
    "/api/roots/add",
    "/api/roots/remove",
    "/api/auto/check",
]

# 类型非法：必须 400 + 可读错误，而不是连接被重置
TYPE_CASES = [
    ("/api/scan", {"root": ROOT, "maxDepth": "abc"}),
    ("/api/scan", {"root": ROOT, "maxDepth": 99}),
    ("/api/scan", {"root": ROOT, "hotMB": {"x": 1}}),
    ("/api/scan", {"root": 123}),
    ("/api/scan", {"root": [ROOT]}),
    ("/api/list", {"path": ["x"]}),
    ("/api/preview", {"paths": "single"}),
    ("/api/config", {"rules": "x"}),
]


def _call(path, raw=None, body=None):
    """返回 (status, payload, error)；连接异常归一为 error 文案。"""
    try:
        st, data = post(path, raw=raw, body=body, timeout=20)
        return st, data, None
    except Exception as ex:  # URLError / 连接重置 / 超时
        return None, None, "%s: %s" % (type(ex).__name__, ex)


def run():
    c = Checker("接口契约 / 畸形输入")

    for path in SAFE_ENDPOINTS:
        for label, raw in NON_OBJECT_BODIES:
            st, data, err = _call(path, raw=raw)
            c.ok("%s × %s 不断连" % (path, label), err is None, err or "")
            if err is not None:
                continue
            c.ok("%s × %s 返回 JSON 对象" % (path, label), isinstance(data, dict), data)
            dumped = json.dumps(data, ensure_ascii=False)
            c.ok("%s × %s 不泄露栈信息" % (path, label), "Traceback" not in dumped, dumped)

    for path, body in TYPE_CASES:
        st, data, err = _call(path, body=body)
        c.ok("%s 类型非法不断连 %r" % (path, body), err is None, err or "")
        if err is not None:
            continue
        c.eq("%s 类型非法返回 400 %r" % (path, body), st, 400)
        c.ok("%s 错误信息可读 %r" % (path, body), bool((data or {}).get("error")), data)

    # 收尾自检：上面所有畸形请求之后，服务仍能正常应答
    st, data, err = _call("/api/auto/check", body={})
    c.ok("畸形请求后服务仍可响应", err is None and st == 200, err or st)

    return c.report()
