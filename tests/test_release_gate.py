# -*- coding: utf-8 -*-
"""发版门禁自身的可靠性测试（2.0.0 收口）。

**防递归约束**：本组绝不调用 `release_gate.main()` 默认路径或 `check_regression()`
—— 完整门禁会拉起 `run_tests.py`，在回归序列里调用它等于无限递归。
这里只验证：静态检查函数、自检子进程（`--self-test` 不含 C6）、以及门禁的
「破坏必被拦下」能力。C5/C6 由 M3 的完整门禁单独执行。

覆盖：
  * 检查器不是「永远返回 True」的空壳：构造规范骨架 → 基线全绿；
    逐项破坏版本/门禁注册/安全面/文档 → 对应检查必须 FAIL。
  * 检查函数是纯函数：对同一 root 幂等，且不修改任何被检文件。
  * 门禁脚本自身零删除面（AST 级，不是字符串匹配）。
"""

import ast
import hashlib
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import release_gate as gate  # noqa: E402
from tests.common import APP, Checker  # noqa: E402


def _hash(rel):
    with open(os.path.join(APP, rel), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def test_module(c):
    c.ok("门禁模块可导入", hasattr(gate, "check_version_sources"))
    c.ok("版本真值可提取", gate._version(APP) is not None, gate._version(APP))
    c.eq("G1–G6 六条门禁函数名齐全", len(gate.GATE_FUNCS), 6)
    c.eq("版本字面量来源 4 处（+server 真值）", len(gate.VERSION_SOURCES), 4)
    for p in gate.PLAN_FILES:
        c.ok("PLAN 文档存在 %s" % p, os.path.isfile(os.path.join(APP, p)))


def test_real_repo(c):
    """对真实仓库跑四项静态检查 —— 收口完成后必须全绿。"""
    for name, fn in (("C1 版本一致性", gate.check_version_sources),
                     ("C2 门禁矩阵在册", gate.check_gate_suite),
                     ("C3 静态安全面", gate.check_static_security),
                     ("C4 文档同步", gate.check_docs)):
        ok, detail = fn(APP)
        c.ok("%s 对真实仓库通过" % name, ok, detail)
        c.ok("%s 返回 (bool, list) 结构" % name,
             isinstance(ok, bool) and isinstance(detail, list), type(detail))


def test_pure(c):
    """检查器只读：调用前后被检文件内容不变。"""
    watched = ["server.py", "static/index.html", "README.md", "CHANGELOG.md",
               "run_tests.py", "cli.py", "core/guide.py", "core/gui.py"]
    before = {p: _hash(p) for p in watched}
    for fn in (gate.check_version_sources, gate.check_gate_suite,
               gate.check_static_security, gate.check_docs):
        fn(APP)
    after = {p: _hash(p) for p in watched}
    c.eq("八次检查调用后所有被检文件哈希不变", after, before)
    a = gate.check_version_sources(APP)
    b = gate.check_version_sources(APP)
    c.eq("同一 root 重复调用结果幂等", a, b)


def test_self_zero_delete(c):
    """门禁脚本自身零删除面：AST 查真实调用节点，字符串常量不算。"""
    src = open(os.path.join(APP, "release_gate.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    banned = {"remove", "unlink", "rmtree", "force_remove", "permanent",
              "recycle", "exec_action", "system", "popen"}
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = (f.attr if isinstance(f, ast.Attribute)
                else f.id if isinstance(f, ast.Name) else "")
        if name in banned:
            hits.append(name)
    c.eq("门禁脚本无删除/命令执行调用", hits, [])
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    c.ok("门禁脚本不 import 删除模块",
         not any("core.actions" in m or m.endswith("actions") for m in imports), imports)


def test_self_test_subprocess(c):
    """--self-test 不含 C6，可在回归序列里安全调用。"""
    src = open(os.path.join(APP, "release_gate.py"), encoding="utf-8").read()
    seg = src[src.index("def self_test():"):src.index("# ---------------- 入口")]
    c.ok("自检函数不含 C6 全量回归调用（防递归）", "check_regression" not in seg)
    c.ok("自检函数不含 C5 产物调用", "check_dual_smoke" not in seg)
    try:
        r = subprocess.run([sys.executable, "release_gate.py", "--self-test"],
                           cwd=APP, capture_output=True, text=True, timeout=120)
    except Exception as e:
        c.ok("--self-test 子进程可运行", False, e)
        return
    c.eq("--self-test 退出码 0", r.returncode, 0, (r.stdout or "")[-400:])
    c.ok("自检输出含「全部通过」", "全部通过" in (r.stdout or ""), (r.stdout or "")[-300:])
    c.ok("自检覆盖 7 类破坏场景", (r.stdout or "").count("被拦下") >= 7)


def test_static_gate_subprocess(c):
    """跳过 C5/C6 的门禁对当前仓库应返回 0（收口后口径一致）。"""
    try:
        r = subprocess.run([sys.executable, "release_gate.py",
                            "--no-regression", "--no-smoke"],
                           cwd=APP, capture_output=True, text=True, timeout=120)
    except Exception as e:
        c.ok("静态门禁子进程可运行", False, e)
        return
    c.eq("静态门禁退出码 0", r.returncode, 0, (r.stdout or "")[-500:])
    c.ok("报告含四项检查", all(("C%d " % i) in (r.stdout or "") for i in range(1, 5)))


def test_skeleton_breaks(c):
    """规范骨架基线全绿；逐项破坏必须被对应检查拦下。"""
    with tempfile.TemporaryDirectory(prefix="adc-gtest-") as td:
        repo = os.path.join(td, "repo")
        os.makedirs(repo)
        gate._skeleton(repo)
        for name, fn in (("C1", gate.check_version_sources),
                         ("C2", gate.check_gate_suite),
                         ("C3", gate.check_static_security),
                         ("C4", gate.check_docs)):
            ok, detail = fn(repo)
            c.ok("骨架基线 %s 全绿" % name, ok, detail)

        cases = [
            ("界面版本不一致", "static/index.html", lambda s: s.replace("v2.0.0", "v9.9.9"),
             gate.check_version_sources),
            ("CHANGELOG 段过期", "CHANGELOG.md", lambda s: "# 更新记录\n\n## 1.0.0 — 2020\n",
             gate.check_version_sources),
            ("门禁组被摘除", "run_tests.py", lambda s: s.replace("test_gate.run()\n", ""),
             gate.check_gate_suite),
            ("测试组漏注册", "run_tests.py", lambda s: s.replace("test_release_gate", ""),
             gate.check_gate_suite),
            ("CLI 注入删除 API", "cli.py", lambda s: s + "\nos.remove('x')\n",
             gate.check_static_security),
            ("引导模块注入命令执行", "core/guide.py", lambda s: s + "\nimport subprocess\n",
             gate.check_static_security),
            ("GUI 壳注入命令执行", "core/gui.py", lambda s: s + "\nimport subprocess\n",
             gate.check_static_security),
            ("README 写死过期组数", "README.md", lambda s: s + "\n跑 16 组 607 项。\n",
             gate.check_docs),
            ("功能编号错乱", "README.md", lambda s: s.replace("2. 预览", "5. 预览"),
             gate.check_docs),
        ]
        for label, rel, mutate, fn in cases:
            orig = gate._read(repo, rel)
            gate._write(repo, rel, mutate(orig))
            ok, _ = fn(repo)
            c.ok("破坏「%s」被拦下" % label, not ok, "检查器漏报")
            gate._write(repo, rel, orig)
        # 恢复后应重新全绿（证明不是残留状态造成的假 FAIL）
        ok, detail = gate.check_docs(repo)
        c.ok("全部恢复后 C4 重新全绿", ok, detail)

        p = os.path.join(repo, "PLAN-1.9.md")
        os.rename(p, p + ".bak")
        ok, _ = gate.check_docs(repo)
        c.ok("破坏「PLAN 文档缺失」被拦下", not ok)
        os.rename(p + ".bak", p)


def test_no_recursion_guard(c):
    """C5/C6 只在完整门禁里执行；测试组不触碰它们。"""
    src = open(os.path.join(APP, "release_gate.py"), encoding="utf-8").read()
    c.ok("C6 调用 run_tests.py", "run_tests.py" in src)
    c.ok("C5 同时校验源码与 exe 形态", "dist" in src and "--version" in src)
    c.ok("默认完整门禁含 C5/C6", 'if not args.no_smoke' in src and 'if not args.no_regression' in src)
    c.ok("本测试组未调用 check_regression", "gate.check_regression(" not in
         open(os.path.abspath(__file__), encoding="utf-8").read().split("def test_no_recursion_guard")[0])


def run():
    c = Checker("发版门禁可靠性（收口版）")
    test_module(c)
    test_real_repo(c)
    test_pure(c)
    test_self_zero_delete(c)
    test_self_test_subprocess(c)
    test_static_gate_subprocess(c)
    test_skeleton_breaks(c)
    test_no_recursion_guard(c)
    return c.report()


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
