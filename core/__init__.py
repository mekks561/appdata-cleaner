# -*- coding: utf-8 -*-
"""AppData 磁盘清理助手 · 核心模块

分层：
    rules   路径判定（唯一判定入口）
    scan    只读扫描与统计
    actions 删除/移动/回收站
    store   状态、令牌、会话
    errors  错误码翻译
"""

from . import rules  # noqa: F401

__all__ = ["rules"]
