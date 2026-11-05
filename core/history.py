# -*- coding: utf-8 -*-
"""操作历史与移动清单（manifest）。

· `logs/clean-YYYYMMDD.jsonl` —— 每行一条操作记录，供「历史」页签读取；
  同时仍写一份人类可读的 `logs/clean-YYYYMMDD.log`（老用户习惯看那个）。
· `logs/manifest-<日期>-<时间>-<随机>.json` —— 每次「移动到指定文件夹」
  逐项记 {from, to, size}，撤销时按它搬回去。

manifestId 会从 HTTP 传来，必须当作**不可信输入**：只允许严格匹配文件名格式，
不允许路径分隔符，防止 ../ 穿越到 logs 之外。
"""

import json
import os
import re
import secrets
import time

MANIFEST_RE = re.compile(r"^manifest-\d{8}-\d{6}-[0-9a-f]{6}\.json$")
HISTORY_RE = re.compile(r"^clean-\d{8}\.jsonl$")
ITEM_KEEP = 80          # 单条记录里最多保留的明细条数


def _day(ts=None):
    return time.strftime("%Y%m%d", time.localtime(ts))


def append(log_dir, record):
    """追加一条历史。任何异常都不影响主流程。"""
    try:
        os.makedirs(log_dir, exist_ok=True)
        rec = dict(record)
        rec.setdefault("ts", time.strftime("%Y-%m-%d %H:%M:%S"))
        rec.setdefault("epoch", int(time.time()))
        items = rec.get("items") or []
        rec["items"] = items[:ITEM_KEEP]
        rec["itemsDropped"] = max(0, len(items) - ITEM_KEEP)
        line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
        with open(os.path.join(log_dir, "clean-%s.jsonl" % _day()), "a", encoding="utf-8") as f:
            f.write(line + "\n")
        return True
    except Exception:
        return False


def append_text(log_dir, header, lines):
    """人类可读的 .log（1.0 格式，保留兼容）。"""
    try:
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "clean-%s.log" % _day()), "a", encoding="utf-8") as f:
            f.write("\n" + header + "\n")
            for ln in lines:
                f.write(ln + "\n")
        return True
    except Exception:
        return False


def read(log_dir, limit=50):
    """按时间倒序读取最近 limit 条。跨多天文件合并。"""
    limit = max(1, min(500, int(limit or 50)))
    try:
        names = sorted([n for n in os.listdir(log_dir) if HISTORY_RE.match(n)], reverse=True)
    except OSError:
        return []
    out = []
    for n in names:
        try:
            with open(os.path.join(log_dir, n), "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            continue
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
            if len(out) >= limit:
                return out
    return out


# ---------------- manifest ----------------

def new_manifest(log_dir, items, root, extra=None):
    """写一份移动清单，返回 manifestId。items = [{from,to,size}]。"""
    mid = "manifest-%s-%s.json" % (time.strftime("%Y%m%d-%H%M%S"), secrets.token_hex(3))
    data = {
        "version": 1,
        "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "root": root,
        "items": items,
    }
    if extra:
        data.update(extra)
    try:
        os.makedirs(log_dir, exist_ok=True)
        p = os.path.join(log_dir, mid)
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(p + ".tmp", p)
    except OSError:
        return ""
    return mid


def load_manifest(log_dir, mid):
    """校验文件名格式后读取。格式不对一律 None。"""
    if not mid or not MANIFEST_RE.match(mid):
        return None
    p = os.path.join(log_dir, mid)
    try:
        if os.path.dirname(os.path.abspath(p)) != os.path.abspath(log_dir):
            return None
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    if not isinstance(d.get("items"), list):
        return None
    return d


def prune_manifests(log_dir, keep=40):
    try:
        names = [n for n in os.listdir(log_dir) if MANIFEST_RE.match(n)]
        if len(names) <= keep:
            return
        files = sorted(((os.path.getmtime(os.path.join(log_dir, n)), n) for n in names),
                       reverse=True)
        for _t, n in files[keep:]:
            try:
                os.remove(os.path.join(log_dir, n))
            except OSError:
                pass
    except OSError:
        pass
