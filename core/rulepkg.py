# -*- coding: utf-8 -*-
"""规则包（1.7 R1）：自定义规则的导出/导入格式与校验。

设计要点（见 PLAN-1.7）：
  · 包体与 config.json 的 rules 字段同构，规则条目复用 config._validate_rules
    这个唯一校验单点 —— 导入的规则与手输、/api/config 走完全相同的约束；
  · format / version 是身份字段，不匹配整包拒绝，不做静默兼容；
  · 本模块只做数据校验与组装，不落盘、不改规则树 —— 应用交给调用方
    （server.py：validate → config.save → rules.apply_user_rules）。
"""

from core import config

PACKAGE_FORMAT = "appdata-cleaner-rules"
PACKAGE_VERSION = 1
# 包体防御性上限：64 条规则每条最多 8 段 + 说明，64 KB 绰绰有余
PACKAGE_MAX_BYTES = 64 * 1024


def build_package(rules, app_version, exported_at=None):
    """把当前规则列表组装成导出包 dict。rules 须是已校验的 CFG["rules"]。"""
    return {
        "format": PACKAGE_FORMAT,
        "version": PACKAGE_VERSION,
        "exportedAt": exported_at or "",
        "appVersion": app_version,
        "count": len(rules or []),
        "rules": list(rules or []),
    }


def validate_package(raw):
    """校验导入包。返回 (合法规则列表, 硬错误列表, 警告列表)。

    硬错误非空 = 整包拒绝，调用方不得动现配置；
    警告 = 个别条目非法被丢弃（与 _validate_rules 的降级语义一致）。
    """
    if not isinstance(raw, dict):
        return [], ["规则包必须是 JSON 对象"], []
    if raw.get("format") != PACKAGE_FORMAT:
        return [], ["规则包格式不正确（format 必须是 %r）" % PACKAGE_FORMAT], []
    if raw.get("version") != PACKAGE_VERSION:
        return (
            [],
            [
                "规则包版本不受支持（version=%r，本程序支持 version=%d）"
                % (raw.get("version"), PACKAGE_VERSION)
            ],
            [],
        )
    rules = raw.get("rules")
    if not isinstance(rules, list):
        return [], ["规则包缺少 rules 数组"], []
    if len(rules) > config.MAX_RULES:
        return [], ["规则包最多包含 %d 条规则（实际 %d）" % (config.MAX_RULES, len(rules))], []

    warnings = []
    declared = raw.get("count")
    if isinstance(declared, int) and not isinstance(declared, bool) and declared != len(rules):
        warnings.append("包内 count=%d 与实际条数 %d 不符，以实际条数为准" % (declared, len(rules)))
    valid, errors = config._validate_rules(rules)
    warnings.extend(errors)
    return valid, [], warnings
