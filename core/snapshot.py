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

1.4.0 M2 增加变化对比（R3）：
· `compare()` 把「上次结果树」与「本次结果树」按 fsutil.key() 对齐，
  产出新增 / 已移除 / 增长 Top N / 缩小 Top N，不新增任何磁盘 IO。
· 语义口径：两棵都是**结果树**，被增量复用的子树尺寸与旧树相同 → 视为无变化；
  本次若为增量扫描则 `approx=True`，由 UI 明示「变化可能低估」并给全量重扫出口。
  删除某目录后重扫仍能正确标记为「已移除」（上层目录每次都实枚举）。
"""

import hashlib
import json
import os
import time

from .fsutil import key

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


# ---------------- 1.4 R3：两次扫描的变化对比 ----------------

def flatten(tree):
    """结果树 → {规范化路径: 摘要}。根节点不参与逐项对比（它的 delta 会霸榜）。

    kids 只到 max_depth，更深的目录靠展开懒加载 —— 所以对比范围天然限定在
    「扫描树里看得见的节点」，不会为了 diff 去多读一次磁盘。
    """
    out = {}

    def rec(node, is_root):
        if not isinstance(node, dict):
            return
        p = node.get("p")
        if p and not is_root:
            out[key(p)] = {
                "p": p,
                "n": node.get("n") or os.path.basename(p),
                "s": int(node.get("s") or 0),
                "f": int(node.get("f") or 0),
                "level": node.get("level") or "data",
                "deletable": node.get("deletable") is not False,
                "reused": bool(node.get("reused")),
            }
        for kid in node.get("kids") or ():
            rec(kid, False)

    rec(tree, True)
    return out


def compare(old_tree, new_tree, top=20, approx=False, baseline_at=None):
    """上次结果树 vs 本次结果树。旧树为空时由调用方直接不调本函数。

    返回结构里 totals 是**全量计数**，四个列表只给前 `top` 条并附总数，
    避免几百个临时目录把接口和页面撑爆。"近似"口径见模块 docstring。
    """
    # 基线可能缺失/损坏：当空树处理，绝不因为对比功能把整次扫描带崩。
    old_tree = old_tree if isinstance(old_tree, dict) else {}
    new_tree = new_tree if isinstance(new_tree, dict) else {}
    old = flatten(old_tree)
    new = flatten(new_tree)
    added, removed, grown, shrunk = [], [], [], []

    for k, n in new.items():
        if k not in old:
            added.append(dict(n, kind="new", delta=n["s"], before=0))

    for k, o in old.items():
        if k not in new:
            removed.append(dict(o, kind="removed", delta=-o["s"], before=o["s"]))

    for k, n in new.items():
        o = old.get(k)
        if not o:
            continue
        d = n["s"] - o["s"]
        if d > 0:
            grown.append(dict(n, kind="grown", delta=d, before=o["s"]))
        elif d < 0:
            shrunk.append(dict(n, kind="shrunk", delta=d, before=o["s"]))

    added.sort(key=lambda x: -x["s"])
    removed.sort(key=lambda x: -x["s"])
    grown.sort(key=lambda x: -x["delta"])
    shrunk.sort(key=lambda x: x["delta"])       # 最负的排最前
    limit = max(1, int(top))

    def cut(items):
        return items[:limit]

    # 只用根节点算总量差：逐节点求和会把父子树重复计入而严重高估。
    delta_bytes = int(new_tree.get("s") or 0) - int(old_tree.get("s") or 0)
    return {
        "hasBaseline": True,
        "baselineAt": baseline_at,
        "approx": bool(approx),
        "new": cut(added), "removed": cut(removed),
        "grown": cut(grown), "shrunk": cut(shrunk),
        "totals": {
            "new": len(added), "removed": len(removed),
            "grown": len(grown), "shrunk": len(shrunk),
            "deltaBytes": delta_bytes,
        },
        "reusedCount": sum(1 for n in new.values() if n["reused"]),
    }
