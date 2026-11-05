# -*- coding: utf-8 -*-
"""扫描快照：把上一轮的扫描树落盘，供下一次增量扫描复用。

设计要点
--------
· 快照 = 扫描树 + 每个节点的目录 mtime（字段 `m`），外加 hot / big 两个候选清单。
· 复用判据只有一条：**目录 mtime 没变** 就整棵子树跳过，不再进目录。
  这是启发式的代价：文件在目录内原地改写（大小变了但目录 mtime 不变）时
  体积会沿用旧值。所以结果里一定带 `incremental` 元信息，界面必须明示，
  并给一个「强制全量重扫」的出口 —— 不给出口的近似值就是在骗人。
· 快照体积上限 20 MB，超了先降到「只存 depth ≤ 2」，还超就不写（宁可下次全量）。
"""

import hashlib
import json
import os
import time

SNAPSHOT_VERSION = 1
MAX_BYTES = 20 * 1024 * 1024
KEEP = 5
DEGRADE_DEPTH = 2


def root_key(root):
    try:
        return os.path.normcase(os.path.abspath(root))
    except Exception:
        return str(root).lower()


def path_for(cache_dir, root):
    h = hashlib.sha1(root_key(root).encode("utf-8")).hexdigest()[:16]
    return os.path.join(cache_dir, "snapshot-%s.json" % h)


def load(cache_dir, root):
    """读回快照；不存在/损坏/版本不符/根目录不匹配 一律返回 None。"""
    p = path_for(cache_dir, root)
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    if d.get("version") != SNAPSHOT_VERSION:
        return None
    if root_key(d.get("root")) != root_key(root):
        return None
    if not d.get("tree"):
        return None
    return d


def _strip(node, depth, limit):
    """裁剪到指定深度：超过 limit 的子节点整个丢掉。"""
    if not isinstance(node, dict):
        return node
    kids = node.get("kids")
    if depth >= limit or kids is None:
        node["kids"] = None
        return node
    node["kids"] = [_strip(k, depth + 1, limit) for k in kids]
    return node


def _dump(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def save(cache_dir, root, tree, hot, big, max_depth, elapsed):
    """写快照。返回 (是否写入, 字节数, 是否降级)。任何异常都静默放弃。"""
    try:
        os.makedirs(cache_dir, exist_ok=True)
    except OSError:
        return False, 0, False

    base = {
        "version": SNAPSHOT_VERSION,
        "root": os.path.abspath(root),
        "maxDepth": max_depth,
        "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed": elapsed,
    }

    payload = dict(base, tree=tree, hot=hot, big=big)
    raw = _dump(payload)
    degraded = False
    if len(raw.encode("utf-8")) > MAX_BYTES:
        degraded = True
        payload = dict(base, tree=_strip(tree, 0, DEGRADE_DEPTH), hot=hot, big=big)
        payload["maxDepth"] = min(max_depth, DEGRADE_DEPTH)
        raw = _dump(payload)
        if len(raw.encode("utf-8")) > MAX_BYTES:
            return False, 0, True

    p = path_for(cache_dir, root)
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(raw)
        os.replace(tmp, p)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False, 0, degraded

    _prune(cache_dir)
    return True, len(raw.encode("utf-8")), degraded


def _prune(cache_dir):
    """只保留最近 KEEP 份快照，避免换着目录扫时无限堆积。"""
    try:
        names = [n for n in os.listdir(cache_dir)
                 if n.startswith("snapshot-") and n.endswith(".json")]
        if len(names) <= KEEP:
            return
        files = sorted(((os.path.getmtime(os.path.join(cache_dir, n)), n) for n in names),
                       reverse=True)
        for _t, n in files[KEEP:]:
            try:
                os.remove(os.path.join(cache_dir, n))
            except OSError:
                pass
    except OSError:
        pass


def drop(cache_dir, root):
    try:
        os.remove(path_for(cache_dir, root))
    except OSError:
        pass
