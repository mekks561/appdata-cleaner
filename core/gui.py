# -*- coding: utf-8 -*-
"""桌面窗口薄壳（2.1 GUI 模式）。

铁律：本模块只是「界面壳」——把现有 static/ 网页套进原生窗口。
不提供任何删除/清理能力：不 import core.actions，不执行命令、不写文件、
不调用任何删除 API。webview 延迟导入，保证 CLI/测试/--web 路径
仍是纯标准库运行。
"""

import sys


def _hide_console():
    """GUI 模式下隐藏伴随的控制台黑窗（控制台版 exe 双击时一闪而过）。

    仅调用 Windows 窗口 API，不涉及任何文件系统/进程/命令操作。
    从 cmd 启动时控制台仍归启动者所有，隐藏仅影响本进程窗口。
    """
    if getattr(sys, "frozen", False):
        try:
            import ctypes

            wh = ctypes.windll.kernel32.GetConsoleWindow()
            if wh:
                ctypes.windll.user32.ShowWindow(wh, 0)  # SW_HIDE
        except Exception:
            pass


def run(url, title):
    """阻塞运行桌面窗口，窗口关闭后返回。失败抛异常，由调用方降级。"""
    import webview  # 延迟导入：仅 GUI 模式才引入第三方依赖

    _hide_console()
    webview.create_window(title, url, width=1100, height=760)
    webview.start()


def supported():
    """环境是否支持桌面窗口（WebView2 可用性不在此处探测，由 run 失败降级）。"""
    try:
        import webview  # noqa: F401

        return True
    except Exception:
        return False
