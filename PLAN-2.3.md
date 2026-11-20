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

### M1 基准先行（决策闸门）—— 已完成

- 构建 onedir 版本，`tools/bench_startup.py` 取中位数：`--version` 口径 onefile 1170 ms / onedir 316 ms；
  GUI 就绪口径（启动 → `/api/info` 可访问 → 结束进程）onefile 1624 ms / onedir 1189 ms，详见 §7。
- **闸门结论：未通过**（两种口径改善分别为 854 ms、435 ms，均 < 1 s）。经确认**保持原阈值与口径不变**，
  但继续 M2 及后续里程碑；数据与决定见 §7。

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

### 7.1 M1 基准实测（2026-11-20，本机、360 开启）

**口径 A：`--version`**（`tools/bench_startup.py`，各 5 次取中位数）

| 形态 | 5 次耗时 | 中位数 |
|---|---|---|
| onefile | 1253 / 1142 / 1184 / 1170 / 1132 ms | 1170 ms |
| onedir | 774 / 326 / 316 / 316 / 309 ms | 316 ms |

改善 **854 ms**（首轮 onedir 774 ms 为一次性冷缓存）。

**口径 B：GUI 就绪**（启动 → `http://127.0.0.1:<port>/api/info` 返回 200 → `taskkill /T /F`，各 5 次取中位数）

| 形态 | 5 次耗时 | 中位数 |
|---|---|---|
| onefile | 2496 / 1624 / 1908 / 1619 / 1621 ms | 1624 ms |
| onedir | 1123 / 1240 / 1445 / 1189 / 1187 ms | 1189 ms |

改善 **435 ms**。

**与 PLAN 中 3.46 s 基线的差异说明**：3.46 s 是 2.2 期间的早期手工观测（含首次冷缓存等冷态因素），
本次两种口径都从进程启动开始计时，故绝对值不可直接相减；两形态在本次同口径下可比。

**闸门判定与决定**：两种口径改善均不足 1 s → **闸门不通过**。阈值与验收口径**不修改**；
经确认继续推进 M2–M4，形态切换的收益按实测披露（约 0.4–0.9 s），不宣称更高。

### 7.2 M2 双形态构建与资源路径验证

- `build_exe.bat` 已改为一次构建两种形态（两条 PyInstaller 命令，参数完全一致，仅 `--onefile` / `--onedir` 不同）。
- 产物：onefile `dist\AppDataCleaner.exe` 14,129,938 字节；onedir `dist\AppDataCleaner\` 194 个文件、
  合计 27,161,679 字节（含 `_internal\`）。清单仍只收录 exe 与安装包，`dist\AppDataCleaner\` 不进 `release.json`。
- **资源路径验证（onedir，`APP_NO_GUI=1` + `APP_PORT=8796`）**：
  `/` 200（30,279 字节）、`/app.js` 200（71,572 字节）、`/style.css` 200（16,397 字节）→
  `--add-data` 落在 `_internal\static` 后由 `sys._MEIPASS` 正常解析，静态资源无 404。
- **同目录语义保持**：`/api/info` 显示 `logDir` / `dataDir` / `configPath` 均指向
  `dist\AppDataCleaner\` 下的 `logs` / `cache` / `config.json`，与 onefile「配置与日志跟 exe 走」一致。
- **GUI 路径**：onedir 启动后窗口标题为 `AppData 磁盘清理助手 v2.2.0`，`/api/info` 200，关进程前后无残留监听。
- 双形态 `--version` 均为 `AppDataCleaner v2.2.0`（版本号在 M4 统一升到 2.3.0）；门禁 6/6 通过。

### 7.3 M3 安装包改目录安装

- `.iss` 的 `[Files]` 由单文件改为 `..\dist\AppDataCleaner\*`（`recursesubdirs createallsubdirs`），
  并 `Excludes` 掉本地运行残留的 `config.json*` / `logs\*` / `cache\*`；README 照旧随包。
- `build_installer.bat` 的存在性检查改为 `dist\AppDataCleaner\AppDataCleaner.exe`。
- **静默卸载默认保留配置**：`InitializeUninstall` 增加 `if UninstallSilent then Exit;` ——
  否则 `/VERYSILENT` 下 `MB_YESNO` 会取默认「是」而误删配置。
- **实测（静默安装到 `%TEMP%\adc_m3`）**：
  - 安装 2,619 ms；装出 `AppDataCleaner.exe`(5,862,752) + `_internal\` + README + 卸载器；
  - `config.json` **未随包安装**（排除生效）；空的 `logs` / `cache` 目录随目录结构创建，属正常；
  - 安装后运行 `--version` 为 `v2.2.0`，就绪 1,238 ms；首次运行在 `{app}` 下生成 `config.json` / `logs`；
  - 静默卸载后程序文件与 `_internal` 全部移除，**`config.json` 保留**。
- **与预期的偏差**：安装包体积由 2.2 的 15,979,597 字节**降为 13,433,775 字节**（onedir 的压缩率更好），
  M3 原本预期「体积上升」不成立，按实测记录。
