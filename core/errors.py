# -*- coding: utf-8 -*-
"""错误码 → 人话。所有面向用户的失败提示都从这里出。

原则：每条提示都要告诉用户「下一步做什么」，不能只说"操作失败"。
"""

# SHFileOperation 返回的 DE_* 码（与 WinError 不是一套）
SHELL_CODES = {
    0x71: "回收站已满或该文件太大，无法放入回收站，可改用永久删除",
    0x78: "拒绝访问：该目录受系统保护，或需要以管理员身份运行",
    0x79: "路径过长，Shell 无法处理，请改用永久删除",
    0x7C: "拒绝访问：该目录受系统保护，或需要以管理员身份运行",
    0x20: "文件正被程序占用，请先关闭对应软件",
    0x21: "文件正被程序占用，请先关闭对应软件",
    0x7D: "源路径不存在，可能已被移动或删除",
    0x7E: "目标路径不存在，请检查移动目标文件夹",
    0x80: "无法删除：目标仍在被使用或权限不足",
    0x81: "无法删除：该目录包含系统文件",
    0x82: "无法读取源文件或磁盘",
    0x83: "目标磁盘已满",
}

# 常见 WinError 的处理建议
_WINERROR = {
    5: "拒绝访问：需要以管理员身份运行，或该目录被系统/其他进程保护",
    2: "路径已不存在（可能已被移动或删除）",
    3: "路径过长或格式不受支持",
    32: "文件正被程序占用，请先关闭对应软件后重试",
    33: "文件正被程序占用（部分锁定），请先关闭对应软件",
    145: "目录不为空（有程序正在写入）",
    206: "路径过长，建议改用永久删除模式",
    267: "路径无效，请检查目标文件夹是否存在",
    112: "磁盘空间不足",
}


def shell_message(code):
    """把 SHFileOperation 的返回码翻译成人话。"""
    if code in SHELL_CODES:
        return SHELL_CODES[code]
    return "回收站返回错误码 %d（可在 logs 中查看详情）" % code


def classify_error(ex, path=""):
    """把异常翻译成「用户能照做」的一句话。"""
    we = getattr(ex, "winerror", None)
    if we is None and getattr(ex, "args", None):
        first = ex.args[0]
        if isinstance(first, int):
            we = first
    if we in _WINERROR:
        return _WINERROR[we]
    if isinstance(ex, PermissionError):
        return _WINERROR[5]
    if isinstance(ex, FileNotFoundError):
        return _WINERROR[2]
    if isinstance(ex, OSError) and getattr(ex, "strerror", None):
        return "操作失败：%s" % ex.strerror
    msg = str(ex) or ex.__class__.__name__
    if any(k in msg for k in ("拒绝", "权限", "占用", "过长")):  # 已是人话
        return msg
    return "操作失败：%s" % msg
