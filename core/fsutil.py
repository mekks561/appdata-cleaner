# -*- coding: utf-8 -*-
"""路径与体积的底层工具：长路径、越界判定、磁盘余量、目录体积。"""

import os
import shutil
import time


def key(p):
    """路径比较键：统一分隔符 + 统一大小写。

    os.scandir 返回的 entry.path 会保留「传入路径」的分隔符风格
    （用户粘 `C:/Users/...` 时得到的是反斜杠+正斜杠混排），
    直接用原字符串做字典键会全部失配 —— 索引必须用这个键。
    """
    if not p:
        return ""
    try:
        return os.path.normcase(os.path.normpath(p))
    except Exception:
        return p.lower().replace("/", "\\")


def safe_path(p):
    """超长路径加长路径前缀，避免 WinError 3。"""
    if not p:
        return p
    if p.startswith("\\\\?\\"):
        return p
    if len(p) > 240:
        return "\\\\?\\" + os.path.abspath(p)
    return p


def inside(root, path):
    """path 是否落在 root 之内（含 root 自身）。用于防越界。"""
    if not root or not path:
        return False
    try:
        r = os.path.abspath(root).rstrip("\\").lower() + "\\"
        p = os.path.abspath(path).lower()
        return p == os.path.abspath(root).lower() or (p + "\\").startswith(r)
    except Exception:
        return False


def dir_usage(path):
    """磁盘可用/总容量。"""
    try:
        u = shutil.disk_usage(path)
        return {"total": u.total, "free": u.free}
    except Exception:
        return {"total": 0, "free": 0}


def tree_size(path, deadline=None):
    """目录（或文件）占用的字节数。

    deadline 为 time.time() 的绝对时刻；超过后停止遍历并返回已累计值
    （返回值不再完整，调用方需自行标记 partial）。
    """
    sp = safe_path(path)
    try:
        if os.path.isdir(sp) and not os.path.islink(sp):
            total = 0
            for dp, _dn, fn in os.walk(sp):
                for f in fn:
                    try:
                        total += os.path.getsize(os.path.join(dp, f))
                    except OSError:
                        pass
                if deadline is not None and time.time() > deadline:
                    return total
            return total
        return os.path.getsize(sp)
    except OSError:
        return 0
