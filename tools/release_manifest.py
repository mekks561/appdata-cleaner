# -*- coding: utf-8 -*-
"""发版产物清单（2.2.0）—— 生成 / 校验 `dist/release.json`。

    python tools/release_manifest.py            # 扫描 dist/ 生成清单
    python tools/release_manifest.py --verify   # 校验清单与磁盘文件一致
    python tools/release_manifest.py --version  # 只打印版本号（供构建脚本读取）
    python tools/release_manifest.py --include dist/额外文件.exe

清单结构：
    {"version": "2.2.0", "generatedAt": "2026-11-14T...", "files": [{"name", "size", "sha256"}]}

设计约束：
  * 纯标准库；不进 exe、不改运行时行为；
  * 版本真值仍只来自 `server.py: APP_VERSION`，本脚本不写第二份字面量；
  * 零删除面：只读 `dist/`，只写 `dist/release.json`；
  * 主产物 `AppDataCleaner.exe` 缺失时直接失败，避免产出「看起来成功」的空清单。
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
MANIFEST = os.path.join(DIST, "release.json")
MAIN_EXE = "AppDataCleaner.exe"

APP_VERSION_RE = re.compile(r'^APP_VERSION\s*=\s*["\']([\d.]+)["\']', re.M)


def read_version():
    """版本真值：server.py 的 APP_VERSION 字面量。"""
    with open(os.path.join(ROOT, "server.py"), encoding="utf-8") as f:
        m = APP_VERSION_RE.search(f.read())
    if not m:
        raise SystemExit("server.py 未找到 APP_VERSION 字面量（版本真值缺失）")
    return m.group(1)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _entry(path):
    name = os.path.basename(path)
    return {"name": name, "size": os.path.getsize(path), "sha256": sha256_of(path)}


def collect(version, extra=()):
    """按固定顺序收录主产物、安装包与显式追加的文件；不存在的可选产物跳过。"""
    candidates = [
        os.path.join(DIST, MAIN_EXE),
        os.path.join(DIST, "AppDataCleaner-Setup-%s.exe" % version),
    ]
    candidates.extend(os.path.abspath(p) for p in extra)

    files, seen = [], set()
    for path in candidates:
        name = os.path.basename(path)
        if not os.path.isfile(path):
            if name == MAIN_EXE:
                raise SystemExit("缺少主产物 dist/%s，先运行 build_exe.bat" % MAIN_EXE)
            continue
        if name in seen:
            continue
        seen.add(name)
        files.append(_entry(path))
    return files


def generate(extra=()):
    version = read_version()
    files = collect(version, extra)
    data = {
        "version": version,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "files": files,
    }
    os.makedirs(DIST, exist_ok=True)
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("已生成 %s（v%s，%d 个产物）" % (os.path.relpath(MANIFEST, ROOT), version, len(files)))
    for item in files:
        print("  %-34s %10d  %s" % (item["name"], item["size"], item["sha256"][:16]))
    return 0


def verify():
    """清单存在、版本一致、含主产物、每个文件哈希/大小与清单一致。"""
    bad = []
    if not os.path.isfile(MANIFEST):
        print("[FAIL] 清单缺失：dist/release.json（先运行 tools/release_manifest.py）")
        return 1
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        print("[FAIL] 清单无法解析：%s" % e)
        return 1

    version = read_version()
    if data.get("version") != version:
        bad.append("清单版本 %r ≠ APP_VERSION %s" % (data.get("version"), version))

    files = data.get("files")
    if not isinstance(files, list) or not files:
        bad.append("files 段为空或类型错误")
        files = []
    names = [x.get("name") for x in files if isinstance(x, dict)]
    if MAIN_EXE not in names:
        bad.append("清单未包含主产物 %s" % MAIN_EXE)

    for item in files:
        if not isinstance(item, dict):
            bad.append("条目类型非法：%r" % (item,))
            continue
        name = item.get("name") or ""
        path = os.path.join(DIST, name)
        if not os.path.isfile(path):
            bad.append("产物缺失：dist/%s" % name)
            continue
        size = os.path.getsize(path)
        if size != item.get("size"):
            bad.append("%s 大小不符：磁盘 %d，清单 %r" % (name, size, item.get("size")))
        digest = sha256_of(path)
        if digest != item.get("sha256"):
            bad.append(
                "%s SHA256 不符：磁盘 %s，清单 %s"
                % (name, digest[:16], str(item.get("sha256"))[:16])
            )

    if bad:
        print("[FAIL] 清单校验失败（%d 项）" % len(bad))
        for line in bad:
            print("  - " + line)
        return 1
    print("[OK] 清单校验通过：v%s，%d 个产物" % (version, len(files)))
    return 0


def main():
    ap = argparse.ArgumentParser(description="AppData Cleaner 发版产物清单")
    ap.add_argument("--verify", action="store_true", help="校验清单与磁盘文件是否一致")
    ap.add_argument("--version", action="store_true", help="只打印版本号（供构建脚本读取）")
    ap.add_argument("--include", action="append", default=[], help="额外纳入清单的文件（可重复）")
    args = ap.parse_args()
    if args.version:
        print(read_version())
        return 0
    return verify() if args.verify else generate(args.include)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
