# PLAN-2.2 — 发版与运行时可交付（安装包 + 产物清单 + 门禁 C7）

## 1. 目标

2.1.1 把「验证」变成了可重复流程；本版把「交付」补齐：

1. **可安装**：在 onefile exe 之外提供 Inno Setup 安装包，双击安装即可用，带开始菜单快捷方式与卸载入口。
2. **可校验**：`dist/release.json` 记录每个发版产物的文件名、大小、SHA256；下载方与发版方都能一键核对。
3. **可门禁**：发版门禁新增 **C7 发布清单**，清单缺失/版本不符/哈希不一致一律阻断发版。

**不改任何清理语义、API 契约、CLI 参数与 G1–G6 安全不变量。**

## 2. 范围与定案

| 事项 | 定案 |
|---|---|
| 发版形态 | **exe + 安装包**；**不做代码签名**（当前无证书，在 Release notes 披露） |
| `requires-python` | **保持 `>=3.9`**，不改代码、不改下限 |
| 跨盘根（如 `D:\Downloads`） | **本版不做**，留 2.3 待定（信任边界仍限用户目录内） |
| Inno Setup 依赖 | 仅**构建安装包时**需要，不进入 exe/运行期；CI 不安装 |
| Nuitka A/B | 已实测（见 §7）→ **不采用**，继续 PyInstaller |

## 3. 技术设计

### 3.1 产物清单 `tools/release_manifest.py`（纯标准库）

```
python tools/release_manifest.py           # 扫描 dist/ 生成 dist/release.json
python tools/release_manifest.py --verify  # 校验清单与磁盘文件一致
```

- 收录规则：`dist/AppDataCleaner.exe` 与 `dist/AppDataCleaner-Setup-<ver>.exe`（存在才收），
  以及显式传入的额外文件（`--include` 可重复）。清单自身不收录。
- 结构：`{ "version": APP_VERSION, "generatedAt": ISO8601, "files": [{name,size,sha256}] }`。
- `--verify`：清单必须存在、`version` 等于 `server.py: APP_VERSION`、每个文件存在且 SHA256/大小一致；
  不一致退出码 `1`。
- **零删除面**：只读 `dist/`、只写 `dist/release.json`。

### 3.2 门禁 C7 发布清单

- `check_manifest(root)` 纯函数（只读），并入 `release_gate.py`：
  1. `dist/release.json` 存在且可解析；
  2. `version` 等于 `APP_VERSION`；
  3. `files` 含 `AppDataCleaner.exe`；
  4. 每个条目对应文件存在、SHA256 与清单一致。
- 加入 `--self-test` 破坏矩阵：清单缺失 / 版本不符 / 哈希不符 / 缺主产物，四类破坏必须被拦下。
- 新增 `--no-manifest` 开关：仅用于**未构建产物**的静态检查场景（`tests/test_release_gate.py`
  的静态门禁子进程用它，避免 CI 的 pytest 阶段（早于构建）误报）。

### 3.3 安装包

- `installer/AppDataCleaner.iss`（Inno Setup 6）+ `build_installer.bat`：
  - `build_installer.bat` 从 `server.py` 读版本（不写第二份版本字面量），`ISCC /DAppVersion=<ver>` 传入；
  - 输出 `dist\AppDataCleaner-Setup-<ver>.exe`；
  - `ISCC.exe` 缺失时打印安装指引并退出码 `1`（**不自动安装第三方软件**）；
  - 构建成功后自动调用 `tools/release_manifest.py` 刷新清单。
- 安装位置：`{localappdata}\Programs\AppDataCleaner`（`PrivilegesRequired=lowest`，无需管理员）；
- 快捷方式：开始菜单 + 可选桌面；
- 卸载：**默认保留** `config.json` / `logs/`，卸载时询问「是否同时删除配置与历史日志？」（默认「否」）。

### 3.4 CI 与构建脚本

- `build_exe.bat` 末尾生成 `dist/release.json`（本地一键构建即得清单）。
- CI：`构建 exe` 之后加一步 `python tools/release_manifest.py`，随后 `python release_gate.py --no-regression`
  即包含 C7；**CI 不安装 Inno Setup**（安装包仅本地/发版时构建）。

## 4. 安全评审

1. **门禁零删除面不破**：C7 只读 `dist/`，`release_manifest.py` 只写 `dist/release.json`；
   AST 自检继续覆盖 `release_gate.py`。
2. **安装包不含提权动作**：安装器以当前用户权限运行，不写 `Program Files`、不改系统目录、
   不注册服务、不写注册表启动项；卸载默认保留用户配置与历史。
3. **清单不引入信任链**：SHA256 只证明「下载到的字节与发版时一致」，不等于代码签名；
   Release notes 明确写出「未签名，首次运行可能触发 SmartScreen」。
4. **运行时零依赖不变**：`tools/` 与 `installer/` 均不进 exe；`requirements-build.txt` 不变。

## 5. 里程碑

### M1 文档与版本
- 本文件 + `PLAN_FILES` 注册；版本 2.2.0 贯通 6 处（server/界面/两个 bat/README/CHANGELOG）。

### M2 清单与门禁
- `tools/release_manifest.py`（生成 + `--verify`）；
- `release_gate.py` C7 + `--no-manifest` + 自检破坏矩阵；
- `tests/test_release_gate.py` 增加 C7 骨架基线与破坏用例（不新增测试组）。

### M3 安装包与 CI
- `installer/AppDataCleaner.iss`、`build_installer.bat`；
- `build_exe.bat` 末尾生成清单；CI 增加清单步骤。

### M4 验证与发版
- Ruff → `run_tests.py` → `pytest` → 构建 exe → 生成清单 → 完整门禁（含 C7）→ `--self-test`；
- 分提交推送，CI 全绿后打 `v2.2.0` tag；
- Nuitka 评估结论见 §7：实测后**不采用**，继续 PyInstaller。

## 6. 验收标准

1. `python tools/release_manifest.py` 生成 `dist/release.json`，`--verify` 退出码 `0`；
   手工改一个字节后 `--verify` 必须失败。
2. `python release_gate.py` 的 C1–C7 全绿；删掉 `dist/release.json` 后 C7 必须 FAIL。
3. `python release_gate.py --self-test` 通过，且包含 C7 的破坏场景。
4. `python run_tests.py` 与 `python -m pytest` 全绿；Ruff check/format 干净。
5. `dist\AppDataCleaner.exe --version` 输出 `v2.2.0`。
6. 有 Inno Setup 6 的机器上 `build_installer.bat` 产出 `dist\AppDataCleaner-Setup-2.2.0.exe`，
   并能安装/卸载（卸载默认保留配置与日志）。
7. 安装包**未做代码签名**，在 Release notes 与本文中如实披露。

## 7. 偏差记录（实施回写）

- **Inno Setup 后补安装并已出包**：初次因缺 `ISCC.exe`，脚本按设计打印指引并以 `1` 退出，exe 先发版
  （C7 只要求 `AppDataCleaner.exe` 必在，安装包存在时自动纳入清单）。随后用 `winget install
  JRSoftware.InnoSetup` 安装 6.7.3（**按用户安装**，落在 `%LOCALAPPDATA%\Programs\Inno Setup 6`），
  原脚本只探测两处 Program Files 因而漏检 —— 已补 LocalAppData 探测（`3474189`）。
- **架构标识告警已修**：`.iss` 的 `ArchitecturesAllowed` / `ArchitecturesInstallIn64BitMode`
  由 `x64` 改为 `x64compatible`（`e8b1278`，Inno 6.3+ 推荐写法），重建后编译零告警。
- **发版载体**：tag `v2.2.0` → 提交 `4eb943d`（2026-11-14）；其后 `30e1b3e`（Nuitka 结论）、
  `3474189`、`e8b1278` 为文档与构建脚本补丁，三次 CI 均 success
  （`43696855429` / `43699997110` / `43700198849`）。
- **Release v2.2.0 资产（3 个，均已完成下载回环校验）**：
  - `AppDataCleaner.exe` 14,130,259 字节，SHA256 `1E596C5CA119F4148B9E4AA640BC72E7D5712012BBC2718430E2E5B2C45A8AD9`
  - `AppDataCleaner-Setup-2.2.0.exe` 15,979,597 字节，SHA256 `B183265394EF5AD9AB516ADB614AEB9ADDEFDE33380890038C2FE1F44A2F5F52`
  - `release.json` 422 字节（两个产物的版本/大小/SHA256 清单）
  - 安装包与清单在 `x64compatible` 修复后重传，旧资产（含早期 249 字节清单）已删除。
- **验收实测**：门禁 C1–C7 全绿、`--self-test` 通过（含 C7 破坏场景）、`run_tests.py` 21 组全绿、
  `pytest` 22 用例 + 覆盖率 77%（门槛 75%）、双形态 `--version` 均为 `v2.2.0`。
- **未做**：安装包**无代码签名**；安装/卸载的实机流程（静默安装到临时目录 → 运行 → 卸载）尚未执行，
  仅按脚本设计保证「卸载默认保留配置与日志」。
- **Nuitka 评估（已实测，结论：不采用）**：环境为 Windows、360 安全卫士开启、无 MSVC；
  使用 Nuitka 4.3.5 + 便携版 CPython 3.13.18 + Zig 0.17.1。
  - **构建前提**：Store 版 Python 在链接阶段失败（`WindowsApps\libs` 拒绝访问），
    必须换正规 / 便携 CPython；`--enable-plugin=pywebview` 可正常打包 `static/` 与 webview 资源。
  - **产物**：目录形态 35.5 MB / 40 个文件（其中 exe 16.0 MB）；PyInstaller onefile 为 14,130,259 字节。
  - **启动耗时**：未能测出——产物被 360 安全卫士秒删（工作区与 `%TEMP%` 均可复现，启动报 WinError 5）；
    同机 PyInstaller 产物正常运行，冷启动 3 次为 3572/3377/3416 ms。
  - **构建耗时**：C 编译 85 个文件，单次超过 10 分钟，构建目录 189 MB。
  - **结论**：2.3 及之后继续 PyInstaller；若要优化启动，先评估 PyInstaller `onedir`
    （免去每次解压到临时目录），不换打包器。重开 Nuitka 评估的前置条件是代码签名
    与 360/火绒等安全软件白名单。
  - 评估用临时环境（便携 Python、Zig、构建目录与目录联接）已全部清理。
