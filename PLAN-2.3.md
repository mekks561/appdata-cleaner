# PLAN-2.3 — 启动与分发形态优化（安装版改 onedir + 安装/卸载实机验证）

## 1. 目标

2.2.0 把「交付」补齐到可安装、可校验；本版解决交付形态里唯一可量化的问题：**启动慢**。

1. **安装版改 onedir**：onefile 每次启动都要把约 13.5 MB 解压到临时目录，实测冷启动 **3.4 s**
   （本机、360 开启，3 次 3572/3377/3416 ms）。安装包本来就适合目录形态，改为 onedir 后免解压。
2. **便携版保留 onefile**：`AppDataCleaner.exe` 仍作为免安装单文件提供，两种形态并存。
3. **补做安装/卸载实机验证**：2.2.0 只验了「构建 + 卸载保留配置」的脚本设计，未真机走一遍。

**不改清理语义、API 契约、CLI 参数与 G1–G6 安全不变量。**

## 2. 范围与定案

| 事项 | 定案 |
|---|---|
| 安装版载体 | **onedir**（`dist\AppDataCleaner\`，由安装包整体安装） |
| 便携版载体 | **onefile 保留**（`dist\AppDataCleaner.exe`，不进安装包） |
| 跨盘根（如 `D:\Downloads`） | **本版不做**：属信任边界变更，需单独论证（见 §6） |
| Nuitka | **不采用**（2.2 已实测：产物被安全软件秒删、需 C 编译器、体积更大） |
| 代码签名 | **仍不做**（无证书），SmartScreen 提示照旧披露 |
| 运行时依赖 | 不变（纯标准库 + pywebview/WebView2） |

## 3. 技术设计

### 3.1 构建（`build_exe.bat`）

- 一次构建两种形态：`--onefile` → `dist\AppDataCleaner.exe`；`--onedir` → `dist\AppDataCleaner\`。
- 两种形态共用同一份 `server.py` / `static` / 打包参数，避免行为分叉。
- **资源路径风险点（必须验证）**：`--add-data "static;static"` 在 onedir 下的落点是
  `dist\AppDataCleaner\_internal\static`，需确认 `server.py` 的资源定位（`sys._MEIPASS`）与
  `config.json` / `logs/` 的「exe 同目录」逻辑在新形态下都不变；GUI 壳的 pywebview 资源同样要核。

### 3.2 清单与门禁（`release.json` / C7）

- `release.json` **仍只收录 `AppDataCleaner.exe` 与 `AppDataCleaner-Setup-<ver>.exe`**；
  `dist\AppDataCleaner\` 视为**安装包的构建输入**，不单独进清单（避免逐文件哈希膨胀）。
- C7 规则不变（清单存在、版本一致、必含 exe、逐项 SHA256/大小一致），不改 schema。

### 3.3 安装包（`installer/AppDataCleaner.iss`）

- `Source` 由单文件改为目录：`dist\AppDataCleaner\*` → `{app}\*`（`recursesubdirs createallsubdirs`）。
- 安装路径、当前用户权限、开始菜单、卸载保留配置/日志的策略**一律不变**。
- 版本号仍由 `build_installer.bat` 从 `server.py` 读取，不写第二份真值。

### 3.4 启动基准（`tools/bench_startup.py`，纯标准库，不进 exe）

- 对给定 exe 测 5 次冷启动（每次结束进程并清系统文件缓存不可行，故只声明「同机先后对比」），
  输出中位数；用于 onefile vs onedir 的同比，不写入门禁（机器差异大，不做硬阈值）。

## 4. 里程碑

### M1 基准先行（决策闸门）

- 先只构建 onedir 版本，跑 `tools/bench_startup.py` 取中位数，与 onefile 的 3.46 s 对比。
- **闸门**：若中位数改善不足 1 s，暂停改造并把数据回写 §7，回报再定（不做无收益的形态切换）。

### M2 构建与资源验证

- `build_exe.bat` 双形态；onedir 形态 `--version`、静态资源、GUI 开窗、`APP_NO_GUI=1` 全链路核验。

### M3 安装包与验证脚本

- `.iss` 改为目录安装；`build_installer.bat` 保持自动刷新清单；
- 安装包体积与安装耗时记录（预期因目录形态而增大，需确认可接受）。

### M4 实机验证与发版

- 静默安装到临时目录 → 运行 `--version` → 生成 `config.json`/`logs` → 卸载 → 核验快捷方式移除、配置保留；
- 版本 2.3.0 贯通 6 处；Ruff → `run_tests.py` → `pytest` → 双形态构建 → 清单 → 门禁 C1–C7 →
  `--self-test`；CI 全绿后打 `v2.3.0` tag，Release 上传 exe + setup + `release.json`。

## 5. 验收标准

1. `dist\AppDataCleaner\AppDataCleaner.exe --version` 与 `dist\AppDataCleaner.exe --version` 均为 `v2.3.0`。
2. onedir 冷启动中位数**低于** onefile 的 3.46 s，且实测数字写入 §7（不预设幅度）。
3. 安装包安装后从开始菜单可启动；卸载后快捷方式消失，`config.json` / `logs/` 保留（选「否」时）。
4. 门禁 C1–C7 全绿，`--self-test` 通过；回归 21 组与 pytest 全绿。
5. 安装包仍未签名，Release notes 与本文件如实披露。
6. 跨盘根与代码签名**未做**，不作为本版验收项。

## 6. 风险与不做项

- **资源路径回归**：onedir 的 `_internal` 布局若与现有 `sys._MEIPASS` 假设不符，表现是界面白屏/静态 404，
  M2 必须先验 GUI 与 `APP_NO_GUI=1` 两条路径。
- **体积与安装耗时**：目录形态通常大于 onefile，安装包体积会上升；只在 M3 记录数字，不设阈值。
- **误报面不变**：未签名 + 360/火绒误杀风险与 2.2 相同，本版不处理。
- **明确不做**：跨盘根（信任边界）、按根分自动清理、多根并行扫描、窗口图标/尺寸记忆、代码签名、
  Nuitka 重新评估（除非具备证书 + 白名单条件）。

## 7. 偏差记录（实施回写）

（待回写）
