# -*- coding: utf-8 -*-
"""2.1 GUI 启动模式：模式分派、gui 薄壳零删除面、延迟导入、旧契约不回退。

分层：
  单元 —— server._interface_mode 分派矩阵（gui/web/headless、--web 优先级）；
  静态 —— core/gui.py 零删除面、server 顶层不 import webview（延迟导入）、
          --web 旧契约仍在、CLI 动作优先于界面模式。
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402
from core import gui as gui_mod  # noqa: E402
from tests.common import APP, Checker  # noqa: E402


def _read(*parts):
    with open(os.path.join(APP, *parts), "r", encoding="utf-8") as fh:
        return fh.read()


def test_mode_dispatch(c):
    m = server._interface_mode
    c.eq("无参数 → gui", m([], {}), "gui")
    c.eq("--web → web", m(["--web"], {}), "web")
    c.eq("APP_NO_GUI → headless", m([], {"APP_NO_GUI": "1"}), "headless")
    c.eq("--web 优先于 APP_NO_GUI", m(["--web"], {"APP_NO_GUI": "1"}), "web")
    c.eq("--takeover 不改变界面模式", m(["--takeover"], {}), "gui")
    c.eq("空 APP_NO_GUI 值视为未设置", m([], {"APP_NO_GUI": ""}), "gui")


def test_gui_module_zero_deletion(c):
    src = _read("core", "gui.py")
    # 按真实调用/导入语句判定，避免被 docstring 里的文字自指误伤
    top_imports = [ln for ln in src.splitlines() if ln.startswith(("import ", "from "))]
    c.eq(
        "gui.py 顶层不 import core.actions", [ln for ln in top_imports if "core.actions" in ln], []
    )
    c.eq("gui.py 顶层不 import subprocess", [ln for ln in top_imports if "subprocess" in ln], [])
    for tok in (
        "os.system",
        "shutil",
        "force_remove",
        "permanent(",
        "recycle(",
        "exec_action",
        "eval(",
    ):
        c.ok("gui.py 零删除面：无 %s" % tok, tok not in src, tok)

    # ctypes 仅允许 Windows 窗口 API（隐藏控制台），不允许进程/文件操作
    ctypes_calls = re.findall(r"ctypes\.windll\.(\w+)\.(\w+)", src)
    allowed = {("kernel32", "GetConsoleWindow"), ("user32", "ShowWindow")}
    bad = [a for a in ctypes_calls if a not in allowed]
    c.eq("gui.py ctypes 仅窗口 API", bad, [])

    # 延迟导入：import webview 只能出现在函数体内（行首有缩进），
    # 且 server 模块顶层不得导入 webview（见 test_server_lazy_import）
    top_level = [ln for ln in src.splitlines() if ln.startswith("import webview")]
    c.eq("webview 不在模块顶层导入", top_level, [])


def test_server_lazy_import(c):
    src = _read("server.py")
    # 顶层 import 区（无缩进 import 行）不得出现 webview
    top = [ln for ln in src.splitlines() if ln.startswith("import ")]
    c.eq("server 顶层无 import webview", [ln for ln in top if "webview" in ln], [])
    # webbrowser 仍在（--web 旧契约依赖）
    c.ok("server 保留 webbrowser（--web 旧契约）", "import webbrowser" in top)


def test_web_contract_intact(c):
    src = _read("server.py")
    c.ok("--web 参数仍在源码", '"--web"' in src, "--web 缺失")
    c.ok("webbrowser.open 仍被引用", "webbrowser.open" in src)
    c.ok("APP_NO_BROWSER 语义保留", "APP_NO_BROWSER" in src)


def test_cli_takes_priority(c):
    # CLI 动作在 __main__ 里先于界面模式分派，避免 --scan 弹窗口
    src = _read("server.py")
    main_block = src[src.index("if __name__") :]
    c.ok(
        "__main__ 先走 cli.is_cli_invocation",
        main_block.find("cli.is_cli_invocation") < main_block.find("main()"),
    )
    import cli  # noqa: F401

    c.ok("--scan 属于 CLI 动作", cli.is_cli_invocation(["--scan", ".", "--yes"]))


def test_supported(c):
    c.ok("gui.supported() 返回 bool", isinstance(gui_mod.supported(), bool))


def run():
    c = Checker("test_gui（2.1 启动模式）")
    for fn in (
        test_mode_dispatch,
        test_gui_module_zero_deletion,
        test_server_lazy_import,
        test_web_contract_intact,
        test_cli_takes_priority,
        test_supported,
    ):
        fn(c)
    return c.report()


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
