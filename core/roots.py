# -*- coding: utf-8 -*-
"""多根目录注册表（v1.9）：根路径校验单点。

边界与 /api/scan 既有校验完全一致：登记根必须落在 user home 内。
本模块只做字符串规范化 + os.path.isdir 存在性检查（只读），
不递归、不写文件、不执行任何命令。

注册表是 UX 便利（下拉一键切换），不是新的安全闸门 —— /api/scan
仍按既有规则校验任意 home 内路径，旧客户端/CLI/测试不受影响。
"""

import os
import re

MAX_ROOTS = 8
MAX_PATH_LEN = 512
MAX_LABEL_LEN = 32

# 系统保护目录段（小写精确匹配），避免把系统目录登记成扫描根
FORBIDDEN_SEGS = frozenset(
    (
        "windows",
        "program files",
        "program files (x86)",
        "programdata",
        "system volume information",
        "$recycle.bin",
        "system32",
        "syswow64",
        "boot",
        "perflogs",
        "recovery",
        "windowsapps",
    )
)

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def default_root(home):
    """内置默认根，恒存在、不可删，不写进 config.roots。"""
    return os.path.join(home, "AppData")


def norm(path):
    """规范化绝对路径；非法输入返回 None。"""
    if not isinstance(path, str) or not path.strip() or len(path) > MAX_PATH_LEN:
        return None
    p = path.strip()
    if p.startswith("\\\\") or p.startswith("//"):
        return None  # UNC / 网络路径一律拒绝
    if ".." in p.replace("/", "\\").split("\\"):
        return None  # 不接受任何穿越段（规范化也挡不住语义歧义）
    try:
        return os.path.normpath(os.path.abspath(p))
    except Exception:
        return None


def clean_label(label, path):
    """label 纯文本：去控制字符、限长；缺省取目录名。"""
    if isinstance(label, str) and label.strip():
        s = _CONTROL_RE.sub("", label.strip())[:MAX_LABEL_LEN]
        if s:
            return s
    return os.path.basename(path) or path


def _is_nested(a, b):
    """a 与 b 相等或互为父子目录（大小写不敏感）。"""
    a, b = a.lower().rstrip("\\/"), b.lower().rstrip("\\/")
    return a == b or a.startswith(b + "\\") or b.startswith(a + "\\")


def validate_item(path, home, existing, label=None):
    """校验一个待登记根。返回 (item dict, error)；成功时 error 为 None。

    existing: 已登记根（含内置默认根）的路径列表，用于重复/嵌套检查。
    """
    p = norm(path)
    if p is None:
        return None, "路径必须是 %d 字符以内的绝对本地路径，且不含 .. 与 UNC" % MAX_PATH_LEN
    if not os.path.isabs(p):
        return None, "必须是绝对路径"
    drive, tail = os.path.splitdrive(p)
    if not drive or not tail.strip("\\/"):
        return None, "不能是盘符根目录"
    home_n = norm(home)
    if home_n is None or not _inside_home(home_n, p):
        return None, "扫描根必须位于当前用户目录内（%s）" % home
    if p.lower().rstrip("\\/") == home_n.lower().rstrip("\\/"):
        return None, "不能把整个用户目录登记为扫描根"
    segs = [s.lower() for s in p.replace("/", "\\").split("\\") if s]
    if any(s in FORBIDDEN_SEGS for s in segs):
        return None, "系统保护目录不能登记为扫描根"
    for key in ("WINDIR", "SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        sysdir = os.environ.get(key)
        if sysdir and _inside_home(p, sysdir):
            return None, "系统保护目录不能登记为扫描根"
    if not os.path.isdir(p):
        return None, "目录不存在或不可访问"
    for old in existing or []:
        old_n = norm(old)
        if old_n and _is_nested(p, old_n):
            return None, "与已有扫描根重复或相互嵌套"
    return {"path": p, "label": clean_label(label, p)}, None


def _inside_home(home, path):
    """path 是否落在 home 内（含自身）；与 fsutil.inside 同语义，避免反向依赖。"""
    h = home.lower().rstrip("\\/") + "\\"
    p = os.path.abspath(path).lower()
    return p == home.lower().rstrip("\\/") or (p + "\\").startswith(h)


def validate_list(raw, home):
    """校验整份根列表（config._validate_roots 调用点）。

    缺字段/None → 空表（合法，表示只有内置默认根）；
    逐条丢弃非法项并报告，与 rules 的降级语义一致。
    """
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], ["roots 必须是数组"]
    if len(raw) > MAX_ROOTS:
        return [], ["扫描根最多 %d 个" % MAX_ROOTS]
    out, errors, seen = [], [], [default_root(home)]
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append("roots[%d]：必须是对象" % i)
            continue
        r, err = validate_item(item.get("path"), home, seen, item.get("label"))
        if err:
            errors.append("roots[%d]：%s" % (i, err))
            continue
        seen.append(r["path"])
        out.append(r)
    return out, errors
