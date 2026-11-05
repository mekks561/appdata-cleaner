# -*- coding: utf-8 -*-
"""测试公共件：HTTP 调用、测试数据构造、结果统计。"""

import json
import os
import shutil
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765"
APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(APP, "testdata")
MB = 1024 * 1024


def post(path, body=None, headers=None, raw=None, timeout=30):
    data = raw if raw is not None else (json.dumps(body).encode("utf-8") if body is not None else None)
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(BASE + path, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def get(path, timeout=30):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {}


def fresh_testdata():
    """重建一份干净的测试目录，避免上轮清理把数据删光。"""
    if os.path.isdir(ROOT):
        shutil.rmtree(ROOT, ignore_errors=True)
    files = [
        ("Local/FakeApp/Cache/blob1.bin", 12),
        ("Local/FakeApp/Cache/blob2.bin", 8),
        ("Local/FakeApp/Data/db.sqlite", 20),
        ("Local/App2/readonly.txt", 1),
        ("Roaming/OtherApp/settings.json", 1),
        ("Local/MicrosoftEdge/User Data/Cache/x.bin", 2),
        ("Local/Microsoft/Edge/User Data/Local State", 1),
    ]
    for rel, mb in files:
        fp = os.path.join(ROOT, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "wb") as f:
            f.write(b"0" * (mb * MB))
    ro = os.path.join(ROOT, "Local", "App2", "readonly.txt")
    os.chmod(ro, 0o444)
    return ROOT


def scan(root=None, max_depth=3, hot_mb=1, big_mb=5, timeout=90, fresh=False):
    """触发扫描并等待完成，返回结果字典。"""
    root = root or ROOT
    st, body = post("/api/scan", {"root": root, "maxDepth": max_depth,
                                  "hotMB": hot_mb, "bigMB": big_mb, "fresh": fresh})
    if st != 200:
        raise RuntimeError("扫描启动失败: %s %s" % (st, body))
    t0 = time.time()
    while time.time() - t0 < timeout:
        _s, status = get("/api/scan/status")
        if not status.get("running"):
            break
        time.sleep(0.15)
    st, res = get("/api/result")
    if st != 200:
        raise RuntimeError("扫描未产出结果: %s" % res)
    return res


def preview(paths):
    st, body = post("/api/preview", {"paths": paths})
    if st != 200:
        raise RuntimeError("预览失败: %s %s" % (st, body))
    return body


def exec_(token, paths, mode="recycle", dest="", confirm=""):
    st, body = post("/api/exec", {"token": token, "paths": paths, "mode": mode,
                                  "dest": dest, "confirmText": confirm})
    return st, body


class Checker(object):
    def __init__(self, name):
        self.name = name
        self.passed = 0
        self.failed = []

    def ok(self, label, cond, detail=""):
        if cond:
            self.passed += 1
        else:
            self.failed.append("%s%s" % (label, ("　→ " + str(detail)) if detail else ""))
        return bool(cond)

    def eq(self, label, got, want, detail=""):
        return self.ok(label, got == want, detail or "得到 %r，期望 %r" % (got, want))

    def report(self):
        total = self.passed + len(self.failed)
        print("\n[%s] %d/%d 通过" % (self.name, self.passed, total))
        for f in self.failed:
            print("   ✗ %s" % f)
        return not self.failed
