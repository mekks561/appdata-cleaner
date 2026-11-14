# -*- coding: utf-8 -*-
"""发版门禁（2.0.0 收口）—— 唯一发版前置入口，非运行时代码。

    python release_gate.py                    # 完整门禁（默认，含全量回归 + 双形态产物）
    python release_gate.py --no-regression   # 跳过 C6 全量回归
    python release_gate.py --no-smoke        # 跳过 C5 双形态产物检查
    python release_gate.py --self-test       # 门禁自检：构造被破坏的副本，验证能拦下
    python release_gate.py --json            # 结构化输出（报告仍写 logs/gate_report.json）

设计要点：
  * 所有静态检查都是 check_xxx(root) 纯函数（只读，不改被检文件），
    root 指向待检项目根目录；自检因此可以在临时骨架上验证「破坏必被拦下」。
  * 版本真值唯一来源 = server.py 的 APP_VERSION 字面量。
  * 本脚本自身零删除面：只读 + subprocess 调用既有测试/冒烟脚本，
    不 import core.actions、不碰回收站、不提供任何无人值守删除开关。
  * 纯标准库，不进 exe，不改运行时行为。
"""

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile

APP = os.path.dirname(os.path.abspath(__file__))

APP_VERSION_RE = re.compile(r'^APP_VERSION\s*=\s*["\']([\d.]+)["\']', re.M)

# (相对路径, 正则, 显示名)。server.py 单独作为真值，不入此表。
VERSION_SOURCES = (
    ("static/index.html", re.compile(r">v([\d.]+)</button>"), "界面版本按钮"),
    ("启动工具.bat", re.compile(r"v([\d.]+)"), "启动工具.bat"),
    ("以管理员身份启动.bat", re.compile(r"v([\d.]+)"), "以管理员身份启动.bat"),
    ("README.md", re.compile(r"^# AppData 磁盘清理助手 v([\d.]+)", re.M), "README 标题"),
)

CHANGELOG_HEAD_RE = re.compile(r"^## ([\d.]+)\s*[—\-]", re.M)

# 删除面 token（不带括号，避免与本文件自身的常量字面量互相误伤）
DELETE_TOKENS = (
    "os.remove",
    "os.unlink",
    "shutil.rmtree",
    "force_remove",
    "permanent",
    "recycle",
    "exec_action",
)
CORE_DELETE_TOKENS = ("os.remove", "os.unlink", "shutil.rmtree")
GUIDE_FORBIDDEN = (
    ("subprocess", re.compile(r"subprocess")),
    ("os 命令/删除", re.compile(r"os\.(system|popen|remove|rename|rmdir)")),
    ("shutil", re.compile(r"shutil")),
    ("文件读写", re.compile(r"open\(")),
    ("磁盘探测", re.compile(r"os\.(stat|lstat|scandir|listdir|walk)")),
    ("eval/exec", re.compile(r"\b(eval|exec|__import__)\s*\(")),
    ("ctypes/winreg", re.compile(r"ctypes|winreg")),
)

# 2.1 GUI 窗口壳：比 guide 稍宽 —— 允许 ctypes 但只允许隐藏控制台的窗口 API。
GUI_FORBIDDEN = (
    ("subprocess", re.compile(r"subprocess")),
    ("os 命令/删除", re.compile(r"os\.(system|popen|remove|rename|rmdir)")),
    ("shutil", re.compile(r"shutil")),
    ("文件读写", re.compile(r"open\(")),
    ("磁盘探测", re.compile(r"os\.(stat|lstat|scandir|listdir|walk)")),
    ("eval/exec", re.compile(r"\b(eval|exec|__import__)\s*\(")),
    ("winreg", re.compile(r"winreg")),
    ("core.actions 导入", re.compile(r"^\s*(?:from\s+core\.actions|import\s+core\.actions)", re.M)),
)
GUI_CTYPES_ALLOWED = re.compile(
    r"ctypes\.windll\.(?:kernel32\.GetConsoleWindow|user32\.ShowWindow)"
)

GATE_FUNCS = (
    "test_g1_only_safe",
    "test_g2_freshness",
    "test_g3_default_off",
    "test_g4_recoverable",
    "test_g5_cli_no_delete",
    "test_g6_explicit_confirm",
)

PLAN_FILES = (
    "PLAN-1.1.md",
    "PLAN-1.3.md",
    "PLAN-1.4.md",
    "PLAN-1.5.md",
    "PLAN-1.6.md",
    "PLAN-1.7.md",
    "PLAN-1.8.md",
    "PLAN-1.9.md",
    "PLAN-2.0.md",
    "PLAN-2.1.md",
)


# ---------------- 基础读取 ----------------


def _read(root, rel):
    with open(os.path.join(root, rel), "r", encoding="utf-8") as f:
        return f.read()


def _version(root):
    """版本真值：server.py 的 APP_VERSION 字面量。"""
    try:
        m = APP_VERSION_RE.search(_read(root, "server.py"))
    except OSError:
        return None
    return m.group(1) if m else None


# ---------------- C1 版本一致性 ----------------


def check_version_sources(root):
    ver = _version(root)
    if not ver:
        return False, ["server.py 未找到 APP_VERSION 字面量（版本真值缺失）"]
    bad = []
    for rel, pat, label in VERSION_SOURCES:
        try:
            src = _read(root, rel)
        except OSError:
            bad.append("%s 缺失文件：%s" % (label, rel))
            continue
        found = pat.findall(src)
        if not found:
            bad.append("%s 未找到版本字面量（%s）" % (label, rel))
            continue
        wrong = sorted(set(v for v in found if v != ver))
        if wrong:
            bad.append("%s 出现 %s，期望 %s（%s）" % (label, "/".join(wrong), ver, rel))
    try:
        heads = CHANGELOG_HEAD_RE.findall(_read(root, "CHANGELOG.md"))
    except OSError:
        heads = []
    if not heads:
        bad.append("CHANGELOG.md 缺少版本段")
    elif heads[0] != ver:
        bad.append("CHANGELOG.md 首段为 %s，期望 %s" % (heads[0], ver))
    return (not bad), (bad or ["6 处版本字面量 + CHANGELOG 首段统一为 %s" % ver])


# ---------------- C2 门禁矩阵在册 ----------------


def check_gate_suite(root):
    bad = []
    try:
        rt = _read(root, "run_tests.py")
    except OSError:
        return False, ["run_tests.py 缺失"]
    if "test_gate" not in rt or "test_gate.run()" not in rt:
        bad.append("run_tests.py 未注册/未调用 G1–G6 门禁组")
    try:
        tg = _read(root, "tests/test_gate.py")
    except OSError:
        tg = ""
    for fn in GATE_FUNCS:
        if ("def " + fn) not in tg:
            bad.append("test_gate.py 缺少 %s" % fn)
    # tests 目录里每个测试组都必须在 run_tests.py 里注册且真实调用，防止漏跑；
    # 只判名字子串会漏报（注释里提到也算「在册」），故同时要求 g.run() 出现。
    tdir = os.path.join(root, "tests")
    groups = []
    if os.path.isdir(tdir):
        groups = sorted(
            f[:-3] for f in os.listdir(tdir) if f.startswith("test_") and f.endswith(".py")
        )
        missing = [g for g in groups if (g not in rt) or ((g + ".run()") not in rt)]
        if missing:
            bad.append("以下测试组未在 run_tests.py 注册或未被调用：%s" % ", ".join(missing))
    else:
        bad.append("tests/ 目录缺失")
    return (not bad), (bad or ["G1–G6 在位；%d 个测试组全部注册并调用" % len(groups)])


# ---------------- C3 静态安全面 ----------------


def check_static_security(root):
    bad = []
    targets = (
        ("cli.py", DELETE_TOKENS, "CLI 无第二套删除路径（G5）"),
        ("core/autoclean.py", CORE_DELETE_TOKENS, "调度模块零删除调用（G1）"),
    )
    for rel, tokens, label in targets:
        try:
            src = _read(root, rel)
        except OSError:
            bad.append("%s 缺失：%s" % (label, rel))
            continue
        hit = [t for t in tokens if t in src]
        if hit:
            bad.append("%s 命中禁用 token：%s" % (label, ", ".join(hit)))
    try:
        g = _read(root, "core/guide.py")
    except OSError:
        bad.append("core/guide.py 缺失")
    else:
        hit = [name for name, pat in GUIDE_FORBIDDEN if pat.search(g)]
        if hit:
            bad.append("引导模块零执行面被破坏：%s" % ", ".join(hit))
    try:
        gui = _read(root, "core/gui.py")
    except OSError:
        bad.append("core/gui.py 缺失")
    else:
        hit = [name for name, pat in GUI_FORBIDDEN if pat.search(gui)]
        if hit:
            bad.append("GUI 壳零删除面被破坏：%s" % ", ".join(hit))
        # ctypes 只允许「隐藏控制台」的窗口 API，其余调用即违规
        ctype_uses = re.findall(r"ctypes\.[A-Za-z0-9_.]+", gui)
        bad_ctypes = [u for u in ctype_uses if not GUI_CTYPES_ALLOWED.search(u)]
        if bad_ctypes:
            bad.append("GUI 壳 ctypes 越界：%s" % ", ".join(sorted(set(bad_ctypes))))
    return (not bad), (bad or ["cli.py / autoclean.py / guide.py / gui.py 静态安全面干净"])


# ---------------- C4 文档同步 ----------------


def check_docs(root):
    bad = []
    ver = _version(root)
    try:
        rd = _read(root, "README.md")
    except OSError:
        return False, ["README.md 缺失"]

    # 1) 版本路线里「（本版）」只能指向当前版本；杜绝 1.7「本版」这类残留
    for m in re.finditer(r"^> 版本路线：.*$", rd, re.M):
        line = m.group(0)
        if "（本版）" in line and ver and ver not in line:
            bad.append("版本路线仍把旧版本标为「本版」（未出现当前版本 %s）" % ver)
        if ver and ("2.0.0" not in line):
            bad.append("版本路线未提及 2.0.0 收口版")

    # 2) 禁止写死断言数 / 组数（历史上「16 组 607 项」「15 组」反复过期）
    stale = re.findall(r"\d+\s*组\s*\d+\s*项|一键测试（\d+\s*组）|run_tests\.py.*（\d+\s*组）", rd)
    if stale:
        bad.append("README 存在写死的组数口径（应改为描述性写法）：%s" % "; ".join(stale[:3]))

    # 3) 「使用」小节的功能列表编号必须连续（历史上 10→9→10 错乱）
    seg = rd.split("## 使用")
    seg = seg[1].split("## 命令行模式")[0] if len(seg) > 1 else ""
    nums = [int(x) for x in re.findall(r"^(\d+)\. ", seg, re.M)]
    if nums and nums != list(range(1, len(nums) + 1)):
        bad.append("功能列表编号不连续：%s" % nums)

    # 4) PLAN 系列齐全
    missing = [p for p in PLAN_FILES if not os.path.isfile(os.path.join(root, p))]
    if missing:
        bad.append("缺 PLAN 文档：%s" % ", ".join(missing))

    # 5) 文件说明里关键模块不得漏列
    for token in ("core/roots.py", "core/guide.py", "core/rulepkg.py", "release_gate.py"):
        if token not in rd:
            bad.append("文件说明缺 %s" % token)

    return (not bad), (bad or ["README 口径、功能编号、PLAN/文件清单齐全"])


# ---------------- C5 双形态产物 ----------------


def check_dual_smoke(root):
    ver = _version(root)
    if not ver:
        return False, ["版本真值缺失，跳过双形态"]
    want = "AppDataCleaner v" + ver
    bad = []
    try:
        r = subprocess.run(
            [sys.executable, "server.py", "--version"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
        )
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0 or want not in out:
            bad.append("源码形态 --version 异常：exit=%d out=%r" % (r.returncode, out[:120]))
    except Exception as e:
        bad.append("源码形态 --version 调用失败：%s" % e)

    exe = os.path.join(root, "dist", "AppDataCleaner.exe")
    if not os.path.isfile(exe):
        bad.append("exe 形态缺失：dist/AppDataCleaner.exe 未构建（先跑 PyInstaller）")
    else:
        try:
            r = subprocess.run(
                [exe, "--version"], cwd=root, capture_output=True, text=True, timeout=90
            )
            out = (r.stdout or "") + (r.stderr or "")
            if r.returncode != 0 or want not in out:
                bad.append("exe 形态 --version 异常：exit=%d out=%r" % (r.returncode, out[:120]))
        except Exception as e:
            bad.append("exe 形态 --version 调用失败：%s" % e)
    return (not bad), (bad or ["源码 + exe 双形态 --version 均为 %s（零删除）" % want])


# ---------------- C6 全量回归 ----------------


def check_regression(root):
    try:
        r = subprocess.run(
            [sys.executable, "run_tests.py"], cwd=root, capture_output=True, text=True, timeout=1200
        )
    except Exception as e:
        return False, ["全量回归调用失败：%s" % e]
    tail = (r.stdout or "").strip().splitlines()[-4:]
    return (r.returncode == 0), (
        ["全量回归通过：%s" % " | ".join(tail)]
        if r.returncode == 0
        else ["全量回归失败 exit=%d：%s" % (r.returncode, " | ".join(tail))]
    )


# ---------------- 门禁自检 ----------------


def _skeleton(root):
    """构造一份「理想仓库」最小骨架，用于验证检查器本身。"""
    ver = "2.0.0"
    files = {
        "server.py": 'APP_VERSION = "%s"\n' % ver,
        "static/index.html": '<button class="ver">v%s</button>\n' % ver,
        "启动工具.bat": "title AppData Disk Cleaner v%s\n" % ver,
        "以管理员身份启动.bat": "title AppData Disk Cleaner v%s (Administrator)\n" % ver,
        "README.md": (
            "# AppData 磁盘清理助手 v%s\n\n"
            "> 版本路线：1.5 → 1.9 已发布 → **2.0.0 稳定收口版（本版）**。\n\n"
            "## 使用\n\n1. 扫描\n2. 预览\n3. 执行\n\n"
            "## 命令行模式\n\n--version\n\n"
            "## 测试\n\npython run_tests.py\n\n"
            "core/roots.py core/guide.py core/rulepkg.py release_gate.py\n" % ver
        ),
        "CHANGELOG.md": "# 更新记录\n\n## %s — 2026-11-12（收口）\n\n- x\n" % ver,
        "run_tests.py": "from tests import test_gate, test_release_gate\n"
        "test_gate.run()\ntest_release_gate.run()\n",
        "tests/test_gate.py": "".join("def %s(c):\n    pass\n" % f for f in GATE_FUNCS),
        "tests/test_release_gate.py": "def run():\n    return True\n",
        "cli.py": "# thin cli, no deletion\n",
        "core/autoclean.py": "# scheduler, no deletion\n",
        "core/guide.py": "# static guidance only\n",
        "core/gui.py": "# window shell, no deletion\n",
    }
    for p in PLAN_FILES:
        files[p] = "# plan\n"
    for rel, content in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


def _write(root, rel, text):
    with open(os.path.join(root, rel), "w", encoding="utf-8") as f:
        f.write(text)


def self_test():
    """5+ 类破坏场景必须被拦下；同时门禁脚本自身零删除面。"""
    checks = [
        ("C1 版本一致性", check_version_sources),
        ("C2 门禁矩阵在册", check_gate_suite),
        ("C3 静态安全面", check_static_security),
        ("C4 文档同步", check_docs),
    ]
    results = []
    # 门禁脚本自身零删除面：用 AST 查真实调用节点，避免「禁用 token 常量」
    # 这类字符串字面量自指误伤（源码里出现不等于发生了调用）。
    own = _read(APP, "release_gate.py")
    tree = ast.parse(own)
    banned = {
        "remove",
        "unlink",
        "rmtree",
        "force_remove",
        "permanent",
        "recycle",
        "exec_action",
        "system",
        "popen",
    }
    own_bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = (
            f.value.id + "." + f.attr
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
            else f.attr
            if isinstance(f, ast.Attribute)
            else f.id
            if isinstance(f, ast.Name)
            else ""
        )
        if name.split(".")[-1] in banned:
            own_bad.append(name)
    own_bad = sorted(set(own_bad))
    results.append(
        ("自检：门禁脚本自身零删除面", not own_bad, own_bad or ["AST 未发现任何删除/命令执行调用"])
    )

    with tempfile.TemporaryDirectory(prefix="adc-gate-") as td:
        repo = os.path.join(td, "repo")
        os.makedirs(repo)
        _skeleton(repo)

        # 基线：理想仓库四项全绿
        for name, fn in checks:
            ok, detail = fn(repo)
            results.append(("自检基线：" + name, ok, detail))

        # 破坏 1：界面版本号不一致
        orig = _read(repo, "static/index.html")
        _write(repo, "static/index.html", orig.replace("v2.0.0", "v9.9.9"))
        ok, _ = check_version_sources(repo)
        results.append(("破坏①界面版本不一致被拦下", not ok, ["期望 FAIL"]))
        _write(repo, "static/index.html", orig)

        # 破坏 2：CHANGELOG 缺当前版本段
        orig = _read(repo, "CHANGELOG.md")
        _write(repo, "CHANGELOG.md", "# 更新记录\n\n## 1.0.0 — 2020\n\n- old\n")
        ok, _ = check_version_sources(repo)
        results.append(("破坏②CHANGELOG 段过期被拦下", not ok, ["期望 FAIL"]))
        _write(repo, "CHANGELOG.md", orig)

        # 破坏 3：门禁组被从回归里摘掉
        orig = _read(repo, "run_tests.py")
        _write(repo, "run_tests.py", orig.replace("test_gate.run()\n", ""))
        ok, _ = check_gate_suite(repo)
        results.append(("破坏③门禁组被摘除被拦下", not ok, ["期望 FAIL"]))
        _write(repo, "run_tests.py", orig)

        # 破坏 4：CLI 被注入删除 API
        orig = _read(repo, "cli.py")
        _write(repo, "cli.py", orig + "\nos.remove('x')\n")
        ok, _ = check_static_security(repo)
        results.append(("破坏④CLI 注入删除 API 被拦下", not ok, ["期望 FAIL"]))
        _write(repo, "cli.py", orig)

        # 破坏 5：README 写死过期组数
        orig = _read(repo, "README.md")
        _write(repo, "README.md", orig + "\n自动跑 16 组 607 项。\n")
        ok, _ = check_docs(repo)
        results.append(("破坏⑤README 过期组数被拦下", not ok, ["期望 FAIL"]))
        _write(repo, "README.md", orig)

        # 破坏 6：功能列表编号错乱
        _write(repo, "README.md", orig.replace("2. 预览", "4. 预览"))
        ok, _ = check_docs(repo)
        results.append(("破坏⑥功能编号错乱被拦下", not ok, ["期望 FAIL"]))
        _write(repo, "README.md", orig)

        # 破坏 7：PLAN 文档缺失
        p = os.path.join(repo, "PLAN-1.9.md")
        os.rename(p, p + ".bak")
        ok, _ = check_docs(repo)
        results.append(("破坏⑦PLAN 缺失被拦下", not ok, ["期望 FAIL"]))
        os.rename(p + ".bak", p)

    ok_all = all(ok for _, ok, _ in results)
    for name, ok, detail in results:
        print(
            "  [%s] %s%s"
            % (
                "OK" if ok else "FAIL",
                name,
                "" if ok else " → " + "; ".join(str(d) for d in detail),
            )
        )
    print(
        "\n门禁自检：%s（%d/%d）"
        % ("全部通过" if ok_all else "存在失败", sum(1 for _, ok, _ in results if ok), len(results))
    )
    return 0 if ok_all else 1


# ---------------- 入口 ----------------


def main():
    ap = argparse.ArgumentParser(description="AppData Cleaner 发版门禁")
    ap.add_argument("--no-regression", action="store_true", help="跳过 C6 全量回归")
    ap.add_argument("--no-smoke", action="store_true", help="跳过 C5 双形态产物检查")
    ap.add_argument("--self-test", action="store_true", help="门禁自检（构造破坏副本）")
    ap.add_argument("--json", action="store_true", help="结构化输出报告")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    checks = [
        ("C1 版本一致性", check_version_sources),
        ("C2 门禁矩阵在册", check_gate_suite),
        ("C3 静态安全面", check_static_security),
        ("C4 文档同步", check_docs),
    ]
    if not args.no_smoke:
        checks.append(("C5 双形态产物", check_dual_smoke))
    if not args.no_regression:
        checks.append(("C6 全量回归", check_regression))

    report = {"version": _version(APP), "checks": []}
    for name, fn in checks:
        try:
            ok, detail = fn(APP)
        except Exception as e:  # 检查器自身异常也算阻断
            ok, detail = False, ["检查器异常：%s: %s" % (type(e).__name__, e)]
        report["checks"].append({"name": name, "ok": ok, "detail": detail})
        print("  [%s] %s" % ("OK" if ok else "FAIL", name))
        for line in detail:
            print("        " + str(line))

    report["ok"] = all(x["ok"] for x in report["checks"])
    log_dir = os.path.join(APP, "logs")
    if os.path.isdir(log_dir):
        with open(os.path.join(log_dir, "gate_report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    print(
        "\n发版门禁：%s（%d/%d 项）"
        % (
            "全部通过，允许发版" if report["ok"] else "存在阻断项，禁止发版",
            sum(1 for x in report["checks"] if x["ok"]),
            len(report["checks"]),
        )
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    # 非中文区域的 Windows 控制台（如 CI 上的 cp1252）直接打印中文会 UnicodeEncodeError
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
