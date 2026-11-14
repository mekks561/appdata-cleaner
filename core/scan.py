# -*- coding: utf-8 -*-
"""只读扫描：递归统计体积，产出目录排行与大文件排行。

相对 1.0 的改动：
  · 进度计数改为「按目录批量提交」，不再每读一个文件抢一次全局锁；
  · 排行改为「先全量计数、最后取前 N」，不会再因为遍历顺序丢掉真正的大目录；
  · 输出带 level / deletable / note，前端不再自己判断能不能删；
  · 截断不再静默：同时返回 shown / total。

1.2.0 增加增量扫描（R6）：
  · 目录 mtime 未变 → 直接复用上次快照的整棵子树，不再进目录；
  · hot / big 候选同样从快照回放，否则复用会把大目录从排行里弄丢；
  · 结果必须带 `incremental` 元信息，让界面能明示「这是近似值」。

1.4.0 M2 增加并行分片提速（R2）与变化对比（R3）：
  · 1.2 起就已有 4-worker 并行，但只按 AppData 的 3 个顶层目录分片，
    Local 一家独大导致负载严重不均。R2 把分片下沉到 depth==1 的二级目录，
    子任务纯递归、不再向线程池提交（避免嵌套等待）。
  · worker 数由 APP_SCAN_WORKERS 控制，=1 时完全退回串行路径，
    输出结构与字段与旧版逐字一致（增量复用 / 快照 / 排行 / 截断统计不变）。
  · 扫描开始时把旧快照留作 diff 基准，扫描结束前与新树按 key 对齐算出变化。

1.4.0 M3 增加全树扩展名聚合（R4）：
  · 每个目录节点在原本就要做的 scandir 里顺带按扩展名归集本层文件，零额外 IO；
  · 目录分布归属到「结果树最深层可见目录」，更深的目录并入同一归属，条目数有界；
  · 增量复用的子树本轮没有进去读文件 → 沿用上次快照的整份统计并标 approx，
    与 R3「复用即无变化」的近似口径一致；强制全量重扫即可刷新。
"""

import heapq
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import rules, snapshot
from .fsutil import dir_usage, key, safe_path, tree_size

HOT_SHOW = 400  # 排行展示上限
BIG_SHOW = 300  # 大文件展示上限
HOT_KEEP = 4000  # 内存中最多保留的候选数
BIG_KEEP = 3000
ERROR_KEEP = 500
PROGRESS_BATCH = 8192  # 每累计这么多文件提交一次进度

# 1.4 R4：全树扩展名聚合的展示上限（聚合本身不限类型数，只裁剪输出）
EXT_SHOW = 40
EXT_DIR_SHOW = 8
NO_EXT = "(无后缀)"  # 与前端 extOf() 的口径一致

# 候选收集下限：与界面阈值解耦，保证「这次用 200MB 扫、下次用 10MB 扫」
# 复用快照时不会漏掉 10~200MB 之间的目录。
HOT_FLOOR = 10 * 1024 * 1024
BIG_FLOOR = 50 * 1024 * 1024

# 子树「冷却时间」：上一轮记录的最新修改时间距今不足这么多秒，就不复用它的旧统计
COLD_SECONDS = 600

# 1.4 R2：只在 depth==1 把二级目录拆成独立任务，让并行度不再受限于 3 个顶层目录。
PARALLEL_DEPTH = 1
# 真实 AppData（70.1GB / 55.9 万文件）带预热实测：1w 9.57s、4w 5.57s、
# 8w 6.19s、12w 6.79s、16w 6.80s —— 4 档最快且线程争用最小。
DEFAULT_WORKERS = 4


def scan_workers():
    """并行 worker 数。APP_SCAN_WORKERS=1 是官方单线程回退开关（PLAN-1.4 风险 2）。"""
    try:
        n = int(os.environ.get("APP_SCAN_WORKERS") or DEFAULT_WORKERS)
    except (TypeError, ValueError):
        n = DEFAULT_WORKERS
    return max(1, min(16, n))


class ScanCtx(object):
    def __init__(self, root, max_depth, hot_threshold, big_threshold):
        self.root = root
        self.max_depth = max_depth
        self.hot_threshold = hot_threshold  # 上报阈值
        self.big_threshold = big_threshold

        self.files = 0
        self.bytes = 0
        self.errors = []
        self.error_total = 0
        self.hot = []
        self.big = []
        self.total_hot = 0
        self.total_big = 0

        # 1.4 R4：全树扩展名聚合（ext -> {s, f, dirs: {可见目录: [s, f]}}）
        self.ext = {}

        # 增量
        self.prev = None
        self.prev_max = 0
        self.reused_keys = set()
        self.reused_dirs = 0
        self.reused_bytes = 0

        self.start = time.time()
        self.now = self.start
        self.elapsed = 0.0
        self.phase = "准备中"
        self.done = False
        self.lock = threading.Lock()

    # ---- 进度 ----
    def add_progress(self, files, size):
        with self.lock:
            self.files += files
            self.bytes += size

    # ---- 异常 ----
    def note_error(self, path, kind, msg):
        with self.lock:
            self.error_total += 1
            if len(self.errors) < ERROR_KEEP:
                self.errors.append({"p": path, "kind": kind, "msg": msg})

    # ---- 排行 ----
    def add_hot(self, item):
        with self.lock:
            if item["s"] >= self.hot_threshold:
                self.total_hot += 1
            if item["s"] < HOT_FLOOR:
                return
            self.hot.append(item)
            if len(self.hot) > HOT_KEEP:
                self.hot = heapq.nlargest(HOT_KEEP // 2, self.hot, key=lambda x: x["s"])

    def add_big(self, item):
        with self.lock:
            if item["s"] >= self.big_threshold:
                self.total_big += 1
            if item["s"] < BIG_FLOOR:
                return
            self.big.append(item)
            if len(self.big) > BIG_KEEP:
                self.big = heapq.nlargest(BIG_KEEP // 2, self.big, key=lambda x: x["s"])

    # ---- 全树扩展名聚合（1.4 R4） ----
    def add_ext(self, local, owner):
        """按目录批量提交本层文件的扩展名统计。

        与 add_progress 同思路：每目录只抢一次锁，不给每个文件加锁。
        owner 是「结果树最深层可见目录」的路径，用来回答「这个类型都藏在哪」。
        """
        if not local:
            return
        with self.lock:
            for ext, (sz, cnt) in local.items():
                g = self.ext.get(ext)
                if g is None:
                    g = {"s": 0, "f": 0, "dirs": {}}
                    self.ext[ext] = g
                g["s"] += sz
                g["f"] += cnt
                d = g["dirs"].setdefault(owner, [0, 0])
                d[0] += sz
                d[1] += cnt

    # ---- 增量 ----
    def note_reuse(self, path, size, files):
        with self.lock:
            self.reused_keys.add(key(path))
            self.reused_dirs += 1
            self.reused_bytes += size or 0


def _node(path, depth, ctx):
    info = rules.classify(path)
    return {
        "n": os.path.basename(path) or path,
        "p": path,
        "s": 0,
        "f": 0,
        "d": 0,
        "m": 0,
        "level": info["level"],
        "cat": info["level"],  # 兼容 1.0 前端字段
        "deletable": info["deletable"],
        "note": info["note"],
        "vendor": info["vendor"],
        "kids": [] if depth < ctx.max_depth else None,
        "err": None,
    }


def _can_reuse(prev, mtime, depth, max_depth, prev_max, now):
    """能否整棵子树复用。

    两条硬约束，缺一不可：
      1) **只在最深一层复用**。上层目录每次都必须真的枚举 —— 目录 mtime 只在
         「自己内部增删子项」时变化，子目录里新增文件不会改父目录 mtime，
         所以在浅层复用会漏掉 `Local\\App07\\Cache` 这类深层新增。
      2) **最近动过的子树一律重扫**。`hm` 是上一轮记录的「子树内最新 mtime」，
         它离现在不足 COLD_SECONDS 就说明这块还在被写，不能信旧值。

    即使满足两条，文件在原目录内被原地改写（大小变了但目录 mtime 不变）仍可能
    沿用旧值 —— 所以结果必须带 incremental 标记，界面要给「强制全量重扫」。
    """
    if not prev or not mtime:
        return False
    if prev.get("m") != mtime:
        return False
    if now - (prev.get("hm") or 0) < COLD_SECONDS:
        return False
    # 只允许在最深一层复用；上层必须实枚举，否则漏掉深层新增
    if depth != max_depth:
        return False
    return prev_max >= max_depth


def _replay(ctx, items, adder, seen, floor):
    """把快照里的候选重新计入（只补挂在被复用子树下的那些）。

    `seen` 由调用方传入，避免和现场走查得到的候选重复计数。
    注意不能靠 `adder is ctx.add_hot` 判断是哪一类 —— 每次取属性都会生成
    新的绑定方法对象，恒为 False。
    """
    if not items or not ctx.reused_keys:
        return 0
    n = 0
    for h in items:
        k = key(h.get("p") or "")
        if not k or k in seen:
            continue
        if h.get("s", 0) < floor:
            continue
        if not _under_reused(k, ctx.reused_keys):
            continue
        adder(dict(h, reused=True))
        seen.add(k)
        n += 1
    return n


def _under_reused(k, reused):
    """k 自身或任一祖先目录落在复用集合内。"""
    if k in reused:
        return True
    cur = k
    while True:
        i = cur.rfind("\\")
        if i <= 2:
            return False
        cur = cur[:i]
        if cur in reused:
            return True


def walk(node_path, depth, ctx, prev=None, mtime=0, executor=None, ext_owner=None):
    """递归统计，返回节点。kids 只保留到 max_depth 层，更深靠懒加载。

    mtime 由调用方从目录项自带的信息里取（scandir 已经缓存了 stat），
    不要再对每个目录多做一次 os.stat —— 8.6 万个目录实测要多花约 3 秒。
    ext_owner 是 R4 扩展名统计归属的可见祖先目录，只在超出 max_depth 时沿用。
    """
    # Windows 的目录枚举缓存：文件刚新增/删除后，直接 os.stat(dir) 已是新 mtime，
    # 但父目录 scandir 拿到的 DirEntry.stat() 可能仍是旧值。增量复用只信 mtime，
    # 于是会「新增了文件却判定可复用」而漏检（本次并行改造改变时序后稳定复现）。
    # 只对「最深一层 + 旧树里有对应节点」的候选用直接路径复核一次：
    # 复用判定只可能发生在这一层，全量扫描 prev=None 时零额外 IO。
    if depth == ctx.max_depth and prev is not None:
        try:
            real_mt = int(os.stat(safe_path(node_path)).st_mtime)
            if real_mt != mtime:
                mtime = real_mt
        except OSError:
            pass

    node = _node(node_path, depth, ctx)
    node["m"] = mtime

    # 1.4 R4：本层文件归属到「结果树最深层可见目录」，更深的目录并入同一归属，
    # 目录分布的条目数因此与扫描树规模同阶，不会随递归深度失控。
    owner = node_path if depth <= ctx.max_depth else (ext_owner or node_path)

    if _can_reuse(prev, mtime, depth, ctx.max_depth, ctx.prev_max, ctx.now):
        node["s"] = prev.get("s", 0)
        node["f"] = prev.get("f", 0)
        node["d"] = prev.get("d", 0)
        node["hm"] = prev.get("hm") or mtime
        node["kids"] = prev.get("kids") if depth < ctx.max_depth else None
        node["reused"] = True
        ctx.note_reuse(node_path, node["s"], node["f"])
        ctx.add_progress(node["f"], node["s"])
        return node

    local_files = 0
    local_bytes = 0
    local_ext = {}
    prev_kids = _prev_kids(prev) if prev else {}

    # 1.4 R2：把 depth==1 的二级目录拆成独立任务。子任务一律 executor=None，
    # 纯递归不再提交任务 —— 否则线程池里的任务等待线程池任务会互相占满。
    parallel = executor is not None and depth == PARALLEL_DEPTH
    pending = []
    agg = {"max_child": 0, "max_hm": mtime}

    def absorb(child, mt):
        # 只在主线程聚合，无需加锁；按 pending 顺序 append，顺序与串行一致。
        node["s"] += child["s"]
        node["f"] += child["f"]
        node["d"] += 1 + child["d"]
        agg["max_child"] = max(agg["max_child"], child["s"])
        agg["max_hm"] = max(agg["max_hm"], child.get("hm") or mt)
        if node["kids"] is not None:
            node["kids"].append(child)

    try:
        with os.scandir(safe_path(node_path)) as it:
            for e in it:
                try:
                    if e.is_junction() or e.is_symlink():
                        continue
                    if e.is_dir(follow_symlinks=False):
                        try:
                            mt = int(e.stat(follow_symlinks=False).st_mtime)
                        except OSError:
                            mt = 0
                        pk = prev_kids.get(key(e.path))
                        if parallel:
                            pending.append((e.path, mt, pk))
                        else:
                            absorb(walk(e.path, depth + 1, ctx, pk, mt, None, owner), mt)
                    elif e.is_file(follow_symlinks=False):
                        st = e.stat(follow_symlinks=False)
                        sz = st.st_size
                        node["s"] += sz
                        node["f"] += 1
                        local_files += 1
                        local_bytes += sz
                        # 1.4 R4：本层归集，节点结束时批量提交，零额外 IO。
                        # 用 rfind 而不是 os.path.splitext：55 万文件下少一次函数
                        # 调用与元组分配；口径与前端 extOf() 一致（i > 0 才算后缀）。
                        i = e.name.rfind(".")
                        ext = e.name[i:].lower() if i > 0 else NO_EXT
                        g = local_ext.setdefault(ext, [0, 0])
                        g[0] += sz
                        g[1] += 1
                        if sz >= BIG_FLOOR:
                            mt = int(st.st_mtime)
                            if mt > agg["max_hm"]:
                                agg["max_hm"] = mt
                            info = rules.classify(e.path)
                            ctx.add_big(
                                {
                                    "p": e.path,
                                    "n": e.name,
                                    "s": sz,
                                    "level": info["level"],
                                    "cat": info["level"],
                                    "deletable": info["deletable"],
                                    "note": info["note"],
                                    "f": 1,
                                    "dir": False,
                                    "mtime": time.strftime("%Y-%m-%d", time.localtime(st.st_mtime)),
                                }
                            )
                        if local_files >= PROGRESS_BATCH:
                            ctx.add_progress(local_files, local_bytes)
                            local_files = local_bytes = 0
                except PermissionError:
                    ctx.note_error(e.path, "denied", "无权限访问")
                    node["err"] = node["err"] or "denied"
                except OSError as ex:
                    kind = "locked" if getattr(ex, "winerror", None) in (32, 33) else "os"
                    ctx.note_error(e.path, kind, str(ex))
                    node["err"] = node["err"] or kind
    except PermissionError:
        ctx.note_error(node_path, "denied", "无权限访问该目录")
        node["err"] = "denied"
    except OSError as ex:
        ctx.note_error(node_path, "os", str(ex))
        node["err"] = "os"

    if pending:
        futures = [
            executor.submit(walk, p, depth + 1, ctx, pk, mt, None, owner) for p, mt, pk in pending
        ]
        for (p, mt, _pk), f in zip(pending, futures):
            try:
                absorb(f.result(), mt)
            except Exception as ex:
                # 单个子树失败不能拖垮整次扫描，与串行路径的错误处理保持一致
                ctx.note_error(p, "os", str(ex))
                node["err"] = node["err"] or "os"

    max_child, max_hm = agg["max_child"], agg["max_hm"]

    if local_files:
        ctx.add_progress(local_files, local_bytes)
    ctx.add_ext(local_ext, owner)

    node["hm"] = max_hm

    # 入围条件：超过阈值，且 depth>=2（Local/Roaming 这类纯容器不进排行）
    # 去重：单个子目录占 75% 以上时只保留子目录，避免父子重复霸榜
    dominated = node["s"] > 0 and (max_child / node["s"]) >= 0.75
    if node["s"] >= HOT_FLOOR and depth >= 2 and not dominated:
        ctx.add_hot(
            {
                "p": node_path,
                "n": node["n"],
                "s": node["s"],
                "f": node["f"],
                "d": node["d"],
                "mx": max_child,
                "level": node["level"],
                "cat": node["level"],
                "deletable": node["deletable"],
                "note": node["note"],
                "err": node["err"],
            }
        )
    return node


def _prev_kids(prev_tree):
    out = {}
    for k in (prev_tree or {}).get("kids") or ():
        try:
            out[key(k["p"])] = k
        except Exception:
            continue
    return out


def ext_summary(ext_map, approx=False, partial=False):
    """ScanCtx.ext → 前端视图数据（Top 类型 + 每类型的目录分布）。

    聚合本身不限类型数，只在这里裁剪输出：类型按体积降序取前 EXT_SHOW 个，
    每个类型只给目录分布前 EXT_DIR_SHOW 条，其余用 otherDirs 披露，绝不静默丢掉。
    pct 以全树总量为分母（不是前 N 名的和），与界面上的「占比」语义一致。
    """
    items = []
    for ext, g in (ext_map or {}).items():
        dirs = sorted((g.get("dirs") or {}).items(), key=lambda kv: -kv[1][0])
        items.append(
            {
                "ext": ext,
                "s": int(g.get("s") or 0),
                "f": int(g.get("f") or 0),
                "dirs": [{"p": p, "s": int(v[0]), "f": int(v[1])} for p, v in dirs[:EXT_DIR_SHOW]],
                "otherDirs": max(0, len(dirs) - EXT_DIR_SHOW),
            }
        )
    items.sort(key=lambda x: (-x["s"], x["ext"]))
    total = sum(x["s"] for x in items)
    shown = items[:EXT_SHOW]
    for x in shown:
        x["pct"] = round(x["s"] * 100.0 / total, 2) if total else 0.0
    return {
        "approx": bool(approx),
        # 旧快照还没有扩展名数据、本轮又是增量时统计只覆盖实扫文件，
        # 会明显低估 —— 必须显式标记，让界面引导「强制全量重扫」。
        "partial": bool(partial),
        "totalBytes": total,
        "totalFiles": sum(x["f"] for x in items),
        "types": len(items),
        "shown": len(shown),
        "items": shown,
    }


def run_scan(
    root, max_depth, hot_mb, big_mb, on_begin=None, on_finish=None, fresh=False, cache_dir=None
):
    """执行一次扫描。on_begin/on_finish 用于与 server 解耦。

    fresh=True 强制全量；cache_dir 为空则不做快照（等价于永远全量）。
    """
    ctx = ScanCtx(root, max_depth, int(hot_mb * 1024 * 1024), int(big_mb * 1024 * 1024))

    # 旧快照既是增量复用的来源，也是 R3 变化对比的基准。
    # fresh 时仍读它做对比，但不拿它做复用（强制全量）。
    baseline = snapshot.load(cache_dir, root) if cache_dir else None
    if not fresh and baseline:
        ctx.prev = baseline
        ctx.prev_max = int(baseline.get("maxDepth") or 0)

    if on_begin:
        on_begin(ctx, root)
    try:
        tops = []
        with os.scandir(safe_path(root)) as it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False) and not e.is_junction():
                        tops.append((e.path, int(e.stat(follow_symlinks=False).st_mtime)))
                except OSError:
                    continue
        tops.sort()

        root_node = {
            "n": os.path.basename(root) or root,
            "p": root,
            "s": 0,
            "f": 0,
            "d": 0,
            "m": 0,
            "hm": 0,
            "level": "data",
            "cat": "data",
            "deletable": True,
            "note": "",
            "vendor": "",
            "kids": [],
            "err": None,
        }
        lock = threading.Lock()
        prev_kids = _prev_kids((ctx.prev or {}).get("tree"))

        def one(item, executor):
            p, mt = item
            n = walk(p, 1, ctx, prev_kids.get(key(p)), mt, executor)
            with lock:
                root_node["s"] += n["s"]
                root_node["f"] += n["f"]
                root_node["d"] += 1 + n["d"]
                root_node["hm"] = max(root_node["hm"], n.get("hm") or mt)
                root_node["kids"].append(n)
            return n

        ctx.phase = "统计各子目录体积"
        nw = scan_workers()
        if nw > 1:
            # 顶层目录串行进入，真正的并行发生在 walk(depth=1) 的二级分片里，
            # 全进程只有一个线程池，不会出现嵌套提交。
            with ThreadPoolExecutor(max_workers=nw) as pool:
                for item in tops:
                    one(item, pool)
        else:
            for item in tops:
                one(item, None)

        # 复用子树时 hot/big 不会自己被记账，从快照回放补上
        seen_hot = set(key(h["p"]) for h in ctx.hot)
        seen_big = set(key(b["p"]) for b in ctx.big)
        _replay(ctx, (ctx.prev or {}).get("hot"), ctx.add_hot, seen_hot, HOT_FLOOR)
        _replay(ctx, (ctx.prev or {}).get("big"), ctx.add_big, seen_big, BIG_FLOOR)

        root_node["kids"].sort(key=lambda x: -x["s"])
        hot = heapq.nlargest(
            HOT_SHOW, [h for h in ctx.hot if h["s"] >= ctx.hot_threshold], key=lambda x: x["s"]
        )
        big = heapq.nlargest(
            BIG_SHOW, [b for b in ctx.big if b["s"] >= ctx.big_threshold], key=lambda x: x["s"]
        )

        # R3：旧树与新树按规范化路径对齐，不新增任何磁盘 IO。
        diff_data = None
        if baseline and baseline.get("tree"):
            try:
                diff_data = snapshot.compare(
                    baseline["tree"],
                    root_node,
                    approx=bool(ctx.reused_keys),
                    baseline_at=baseline.get("createdAt"),
                )
            except Exception:
                diff_data = None

        # R4：全树扩展名聚合。增量复用的子树本轮没进去读文件，沿用上次快照的
        # 整份统计并标 approx（与 R3「复用即无变化」口径一致）；旧快照尚无 ext
        # 字段时只能按本轮实扫结果统计，同样标 approx 让 UI 披露。
        baseline_ext = (baseline or {}).get("ext")
        if ctx.reused_keys and baseline_ext:
            ext_data = dict(baseline_ext, approx=True, partial=False)
        else:
            ext_data = ext_summary(
                ctx.ext, approx=bool(ctx.reused_keys), partial=bool(ctx.reused_keys)
            )

        ctx.elapsed = round(time.time() - ctx.start, 1)
        ctx.phase = "完成"
        ctx.done = True

        saved, saved_bytes, degraded = (False, 0, False)
        if cache_dir:
            # 强制全量时也要写快照：否则「全量一次」等于白白丢掉缓存
            saved, saved_bytes, degraded = snapshot.save(
                cache_dir,
                root,
                root_node,
                list(ctx.hot),
                list(ctx.big),
                max_depth,
                ctx.elapsed,
                ext=ext_data,
            )

        result = {
            "root": root,
            "tree": root_node,
            "hot": hot,
            "big": big,
            "errors": ctx.errors[:200],
            "errorCount": ctx.error_total,
            "fileCount": ctx.files,
            "totalSize": root_node["s"],
            "elapsed": ctx.elapsed,
            "disk": dir_usage(root),
            "scannedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "scannedAtEpoch": int(time.time()),
            "diff": diff_data,
            "ext": ext_data,
            "incremental": {
                "used": bool(ctx.reused_keys),
                "reusedDirs": ctx.reused_dirs,
                "reusedBytes": ctx.reused_bytes,
                "approx": bool(ctx.reused_keys),
                "snapshotAt": (ctx.prev or {}).get("createdAt"),
            },
            "snapshot": {"saved": saved, "bytes": saved_bytes, "degraded": degraded},
            "truncated": {
                "hot": {
                    "shown": len(hot),
                    "total": ctx.total_hot,
                    "truncated": ctx.total_hot > len(hot),
                },
                "big": {
                    "shown": len(big),
                    "total": ctx.total_big,
                    "truncated": ctx.total_big > len(big),
                },
                "errors": {
                    "shown": len(ctx.errors[:200]),
                    "total": ctx.error_total,
                    "truncated": ctx.error_total > 200,
                },
            },
        }
        if on_finish:
            on_finish(result, None)
        return result
    except Exception as ex:
        ctx.phase = "失败: %s" % ex
        if on_finish:
            on_finish(None, ex)
        raise


def index_tree(tree):
    """把扫描树压成 {规范化路径: 体积} 索引，展开时直接命中，不再重复遍历磁盘。

    键必须用 fsutil.key()：scandir 的 entry.path 会继承传入路径的分隔符风格，
    用原始字符串做键会出现「所有子目录都未命中」的退化。
    """
    idx = {}

    def rec(node):
        if not node:
            return
        idx[key(node["p"])] = node["s"]
        for k in node.get("kids") or ():
            rec(k)

    rec(tree)
    return idx


def list_dir(path, size_index=None, budget_ms=1500):
    """列目录内容（展开用，R7）。

    · 子目录体积优先从扫描树索引取，命中即 0 IO；
    · 未命中才现场统计，且受 budget_ms 预算约束，超时返回
      s=None + partial=True，由前端显示「—」，绝不卡住整页。
    """
    t0 = time.time()
    deadline = t0 + budget_ms / 1000.0
    idx = size_index or {}
    timed_out = False
    from_index = 0
    items = []
    try:
        with os.scandir(safe_path(path)) as it:
            for e in it:
                try:
                    if e.is_junction() or e.is_symlink():
                        continue
                    info = rules.classify(e.path)
                    if e.is_dir(follow_symlinks=False):
                        size = idx.get(key(e.path))
                        if size is not None:
                            from_index += 1
                        else:
                            if time.time() < deadline:
                                size = tree_size(e.path, deadline)
                                if time.time() > deadline:
                                    timed_out = True
                            else:
                                timed_out = True
                                size = None
                        items.append(
                            {
                                "n": e.name,
                                "p": e.path,
                                "s": size,
                                "dir": True,
                                "level": info["level"],
                                "cat": info["level"],
                                "deletable": info["deletable"],
                                "note": info["note"],
                            }
                        )
                    else:
                        st = e.stat(follow_symlinks=False)
                        items.append(
                            {
                                "n": e.name,
                                "p": e.path,
                                "s": st.st_size,
                                "dir": False,
                                "mtime": time.strftime("%Y-%m-%d", time.localtime(st.st_mtime)),
                                "level": info["level"],
                                "cat": info["level"],
                                "deletable": info["deletable"],
                                "note": info["note"],
                            }
                        )
                except PermissionError:
                    items.append(
                        {
                            "n": e.name,
                            "p": e.path,
                            "s": None,
                            "dir": True,
                            "level": "protected",
                            "cat": "protected",
                            "deletable": False,
                            "note": "无权限访问",
                            "err": "无权限",
                        }
                    )
                except OSError:
                    continue
    except PermissionError:
        raise PermissionError("无权限访问该目录")

    items.sort(key=lambda x: (-(x["s"] or 0), x["n"].lower()))
    total = len(items)
    return {
        "items": items[:400],
        "total": total,
        "returned": min(total, 400),
        "partial": total > 400 or timed_out,
        "truncatedCount": max(0, total - 400),
        "timedOut": timed_out,
        "fromIndex": from_index,
        "costMs": int((time.time() - t0) * 1000),
    }
