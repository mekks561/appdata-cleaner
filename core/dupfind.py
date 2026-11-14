# -*- coding: utf-8 -*-
"""重复文件检测（1.3 R1）。

三段式流水线，宁可漏报不可误报：

  1) 候选枚举：只走 safe/suspect 目录（protected 子树整棵跳过），
     收集 size >= minSize 的普通文件；硬链接、云盘占位、junction 一律跳过并计数；
  2) 首尾指纹：大小相同才有必要比 —— 读首尾各 64KB 做 MD5 快筛；
  3) 全文件 MD5：幸存组逐字节流式哈希，**哈希全等 + 大小相等**才算重复。

预算：全局哈希秒数上限（默认 30s，可配）。超时返回已完成部分并标 partial，
绝不为了"跑完"而阻塞界面。
"""

import hashlib
import os
import threading
import time

from . import rules

MB = 1024 * 1024
CHUNK = 1024 * 1024
HEAD_TAIL = 64 * 1024
MAX_FILES = 300000
MAX_GROUPS = 500

ATTR_REPARSE = 0x400
ATTR_OFFLINE = 0x1000
ATTR_RECALL = 0x400000  # 云盘占位：读内容会触发下载


# ---------------- 状态（供 /api/dup/status 用） ----------------


class DupState(object):
    def __init__(self):
        self._lock = threading.RLock()
        self._running = False
        self._phase = ""
        self._result = None
        self._error = ""
        self._seq = 0

    def begin(self):
        with self._lock:
            self._running = True
            self._phase = "枚举候选文件"
            self._result = None
            self._error = ""

    def phase(self, text):
        with self._lock:
            self._phase = text

    def finish(self, result):
        with self._lock:
            self._result = result
            self._seq += 1
            self._running = False
            self._phase = "完成"

    def fail(self, msg):
        with self._lock:
            self._error = msg
            self._running = False
            self._phase = "失败"

    def status(self):
        with self._lock:
            return {"running": self._running, "phase": self._phase, "error": self._error}

    def result(self):
        with self._lock:
            return self._result

    def seq(self):
        """结果版本号：每次产出新结果 +1（供测试/前端判定新鲜度）。"""
        with self._lock:
            return self._seq


DUP = DupState()


# ---------------- 底层 ----------------


def _skip_reason(st):
    """返回跳过原因；None 表示是正常候选。

    注意：Windows 上 DirEntry.stat() 的 st_nlink 恒为 0，靠不住 ——
    硬链接判定放到 _drop_hardlinks() 里用 os.stat() 做（只对同尺寸候选组做，代价可控）。
    """
    attrs = getattr(st, "st_file_attributes", 0)
    if attrs & (ATTR_OFFLINE | ATTR_RECALL):
        return "cloud"
    return None


def _drop_hardlinks(group, skipped):
    """同尺寸组内按 (dev, ino) 去重：硬链接是同一份物理数据，不能算两份。

    只在这一步调 os.stat（每文件一次），因为只有「大小完全相同」的文件才值得查。
    """
    seen = {}
    out = []
    for f in group:
        try:
            st = os.stat(f["p"])
        except OSError:
            continue
        if getattr(st, "st_nlink", 1) > 1:
            skipped["hardlink"] += 1
            continue
        key = (getattr(st, "st_dev", 0), getattr(st, "st_ino", 0))
        if key != (0, 0) and key in seen:
            skipped["hardlink"] += 1
            continue
        if key != (0, 0):
            seen[key] = f["p"]
        out.append(f)
    return out


def collect(root, min_size, on_phase=None, cancel=None):
    """枚举候选：返回 (files, skipped)。files 元素 {p,s,m,level}。"""
    files = []
    skipped = {"cloud": 0, "hardlink": 0, "reparse": 0, "denied": 0}
    stack = [root]
    seen = 0
    while stack:
        if cancel and cancel():
            break
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            st = e.stat(follow_symlinks=False)
                            if getattr(st, "st_file_attributes", 0) & ATTR_REPARSE:
                                skipped["reparse"] += 1
                                continue
                            # protected 子树整棵不看；data/suspect/safe 继续下探
                            if rules.classify(e.path).get("level") == rules.LEVEL_PROTECTED:
                                continue
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            st = e.stat(follow_symlinks=False)
                            if st.st_size < min_size:
                                continue
                            why = _skip_reason(st)
                            if why:
                                skipped[why] += 1
                                continue
                            seen += 1
                            if seen > MAX_FILES:
                                continue
                            files.append(
                                {
                                    "p": e.path,
                                    "s": int(st.st_size),
                                    "m": int(st.st_mtime),
                                    "level": rules.classify(d).get("level", ""),
                                }
                            )
                            if on_phase and seen % 5000 == 0:
                                on_phase("枚举候选文件（%d）" % seen)
                    except OSError:
                        skipped["denied"] += 1
        except (OSError, PermissionError):
            skipped["denied"] += 1
    return files, skipped


def partial_digest(path, size):
    """首尾各 64KB 的指纹（小文件直接整份）。"""
    h = hashlib.md5()
    with open(path, "rb") as f:
        h.update(f.read(HEAD_TAIL))
        if size > HEAD_TAIL * 2:
            f.seek(-HEAD_TAIL, os.SEEK_END)
            h.update(f.read(HEAD_TAIL))
    return h.hexdigest()


def full_digest(path, cancel=None):
    """整份 MD5。被取消返回 None。"""
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
            if cancel and cancel():
                return None
    return h.hexdigest()


# ---------------- 主流程 ----------------


def find_duplicates(root, min_size=10 * MB, budget_sec=30.0, cancel=None, on_phase=None):
    """返回 {groups, skipped, partial, scannedFiles, costMs, hashedBytes}。"""
    t0 = time.time()

    if on_phase:
        on_phase("枚举候选文件")
    files, skipped = collect(root, min_size, on_phase=on_phase, cancel=cancel)

    by_size = {}
    for f in files:
        by_size.setdefault(f["s"], []).append(f)
    same_size = []
    for g in by_size.values():
        if len(g) < 2:
            continue
        kept = _drop_hardlinks(g, skipped)
        if len(kept) >= 2:
            same_size.append(kept)

    partial = False
    hashed_bytes = 0

    # 阶段 2：首尾指纹
    if on_phase:
        on_phase("比对外围指纹（%d 组）" % len(same_size))
    by_partial = {}
    for g in same_size:
        if cancel and cancel():
            partial = True
            break
        for f in g:
            if time.time() - t0 > budget_sec:
                partial = True
                break
            try:
                pd = partial_digest(f["p"], f["s"])
            except OSError:
                continue
            hashed_bytes += min(f["s"], HEAD_TAIL * 2)
            by_partial.setdefault((f["s"], pd), []).append(f)
        if partial:
            break

    survive = [g for g in by_partial.values() if len(g) >= 2]

    # 阶段 3：全文件哈希（大文件优先，预算耗尽即停）
    groups = []
    for g in sorted(survive, key=lambda x: -x[0]["s"]):
        if len(groups) >= MAX_GROUPS:
            partial = True
            break
        if time.time() - t0 > budget_sec or (cancel and cancel()):
            partial = True
            break
        if on_phase:
            on_phase("校验内容（%d 份 × %.1f MB）" % (len(g), g[0]["s"] / float(MB)))
        by_full = {}
        for f in g:
            if time.time() - t0 > budget_sec or (cancel and cancel()):
                partial = True
                break
            try:
                d = full_digest(f["p"], cancel=cancel)
            except OSError:
                continue
            if d is None:
                partial = True
                break
            hashed_bytes += f["s"]
            by_full.setdefault(d, []).append(f)
        for digest, items in by_full.items():
            if len(items) < 2:
                continue
            # 终检：大小与哈希都必须全等（双保险，0 误报是硬指标）
            size = items[0]["s"]
            if any(x["s"] != size for x in items):
                continue
            items.sort(key=lambda x: -x["m"])  # 最新修改的排第一
            copies = [
                {
                    "p": x["p"],
                    "m": x["m"],
                    "level": x["level"],
                    "keep": i == 0,
                }
                for i, x in enumerate(items)
            ]
            groups.append(
                {
                    "hash": digest,
                    "size": size,
                    "count": len(copies),
                    "copies": copies,
                    "keepSuggestion": copies[0]["p"],
                    "reclaimable": size * (len(copies) - 1),
                }
            )

    groups.sort(key=lambda g: -g["reclaimable"])
    return {
        "root": root,
        "minSize": min_size,
        "groups": groups,
        "skipped": skipped,
        "partial": partial,
        "scannedFiles": len(files),
        "hashedBytes": hashed_bytes,
        "costMs": int((time.time() - t0) * 1000),
        "reclaimable": sum(g["reclaimable"] for g in groups),
    }
