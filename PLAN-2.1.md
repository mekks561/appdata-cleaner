# PLAN-2.1 — 界面窗口化（pywebview 桌面壳，默认不再开浏览器）

## 1. 目标与定案

用户需求：**界面固化到 EXE，不打算开网页了**。

技术路线（用户已确认）：
- **pywebview 桌面窗口壳**：用 Windows 自带 WebView2 Runtime 渲染现有 `static/` 界面，套成原生窗口；HTTP 服务保留在后台线程。
- **保留 `--web` 开关**：`--web` 仍走「服务 + 系统浏览器」旧路径，用于兼容与调试。
- 版本号：**2.1.0**（2.0.0 已收口，本版为路线外新功能，按 SemVer 次版本递增，不跳号）。

## 2. 技术验证（spike，已完成）

| 验证项 | 结果 |
|---|---|
| pywebview 6.2.1 + pythonnet 3.2.1 在 Python 3.13 下创建/关闭 WebView2 窗口 | 通过（`logs/spike_webview.py`，SPIKE OK） |
| PyInstaller 打包 `--collect-all pythonnet --collect-all webview --collect-all clr_loader` | 通过（13.8 MB 壳 exe 能开窗自动关闭） |

结论：全链路可行。本机 WebView2 Runtime 可用（Win10/11 默认自带）。

## 3. 范围与接口兼容性

**新增**
- `core/gui.py`：桌面窗口薄壳（延迟导入 pywebview；隐藏控制台；窗口标题/尺寸；零删除面）。
- 启动模式分派：无参数 → **GUI 窗口**（新默认）；`--web` → 浏览器（旧行为）；`--version` / CLI 动作 → 不变。
- 环境变量 `APP_NO_GUI=1`：测试/无头场景强制 headless（只起服务，不开任何界面）。

**不变（硬契约）**
- 所有 API 路径、响应字段、错误码一字不改；`/api/*` 全部照旧。
- CLI 参数、退出码、G1–G6 门禁全部照旧。
- `--version` 输出与双形态冒烟口径照旧。
- server 顶层**不 import webview**（延迟导入），CLI / 测试 / `--web` 路径保持纯标准库运行。
- 删除路径零改动。

## 4. 安全评审

1. **GUI 只是「界面壳」**：`core/gui.py` 只做 `create_window + start`、隐藏控制台、异常时降级开浏览器；**不 import core.actions、不 import subprocess/os.system/shutil、不调用任何删除 API**。静态断言纳入门禁 C3 与测试组。
2. **延迟导入**：`import webview` 只在 GUI 模式函数内执行。server 顶层保持纯标准库——CLI/测试/web 模式完全不受第三方代码影响，G5「CLI 源码零删除 API」与「仅标准库」审计面不变。
3. **绑定面不变**：HTTP 服务仍只绑 `127.0.0.1`；WebView2 加载的页面与浏览器同源（`http://127.0.0.1:PORT`），无新增网络暴露。
4. **单实例语义保持**：端口被本项目占用时（`_port_busy()` 返回版本号），GUI 模式直接开窗口指向已有实例，**不 shutdown 别人的服务**；`--takeover` 顶替逻辑不变。
5. **退出联动**：窗口关闭 → `webview.start()` 返回 → `srv.shutdown() + server_close()`，不再有残留监听。
6. **降级兜底**：无 WebView2 Runtime / pywebview 初始化失败时，打印原因并**退回 `--web` 浏览器模式**，保证任何机器上都能用。
7. **第三方供应链**：新增依赖 pywebview + pythonnet（运行时）+ bottle（pywebview 依赖）。这是 2.0 收口「零依赖」承诺的**有意修订**，经用户确认。门禁 C3 对 `core/gui.py` 增加零删除面检查；供应链风险在 Release notes 中披露。

## 5. 里程碑

### M1 后端
- `core/gui.py`：`run(url, title)`（延迟 import webview；create_window 1100×760；`webview.start()`；异常抛给调用方降级）、`hide_console()`。
- `server.py`：
  - `_interface_mode(argv, env)` 纯函数：返回 `"gui" | "web" | "headless"`。
  - `main()`：GUI 模式起服务线程 → 预热/调度照旧 → `gui.run` → `finally` shutdown；端口被本项目占用时开窗口/浏览器指向已有实例；`APP_NO_GUI=1` 强制 headless；`--web` 走旧浏览器路径（含 `APP_NO_BROWSER` 语义不变）。
- 测试环境变量 `APP_NO_GUI=1` 接入 `run_tests.py` 与既有核验/冒烟脚本（仅加 env 一行）。

### M2 测试 + 文档
- `tests/test_gui.py`：模式分派矩阵（gui/web/headless/CLI/--version 优先级）、`core/gui.py` 零删除面静态断言（无 subprocess/os.system/shutil/force_remove/exec_action/ctypes 除窗口 API）、server 顶层延迟导入断言、`--web`/`--version` 旧契约不回退。
- `run_tests.py` 注册新组（放 CLI 组之后、多根组之前）。
- README：启动方式改为「默认桌面窗口；`--web` 开网页」；新增依赖与 WebView2 说明；文件清单补 `core/gui.py`。
- CHANGELOG：2.1.0 段。

### M3 验证与发版
- bump 6 处版本字面量 2.1.0（server/index/README/两个 bat）。
- 全量回归（预计 20 组）。
- 无头 UI 核验（`APP_NO_GUI=1` 起服务，Playwright 照旧核页面）。
- PyInstaller：`--collect-all pythonnet --collect-all webview --collect-all clr_loader`。
- 双形态冒烟（源码 + exe；GUI 形态验证：起窗口自动关闭链路 `logs/gui_smoke.py` 独立跑，不进无头服务冒烟）。
- 发版门禁 `release_gate.py` C1–C6 全绿（C3 已扩 gui 零删除面）。
- 提交 → 推 main/tag `v2.1.0` → GitHub Release + exe + SHA-256 回环。

## 6. 验收标准

1. 双击 exe（源码 `python server.py`）**弹桌面窗口，不再开浏览器**；关窗口即停止服务，无残留监听。
2. `python server.py --web` 仍开浏览器；`python server.py --version` / `--scan/--dry-run/--oneclick` 行为与 2.0.0 完全一致。
3. `APP_NO_GUI=1` 时测试/冒烟/核验全绿，且这些路径不 import pywebview。
4. 全量回归全绿；门禁 C1–C6 全绿（含新增 gui 零删除面）。
5. 无 WebView2 机器上（模拟 `python server.py` 强制降级）能退回浏览器模式并给出原因。
6. exe 资产 SHA-256 下载回环一致。

## 7. 偏差记录（实施回写）

（待回写）
