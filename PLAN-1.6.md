# PLAN-1.6 — CLI 模式（方案 A 顺延后的 1.6.0）

> 状态：**M0/M1/M2/M3 全部交付，v1.6.0 已发布**（2026-11-10）· 前置 `v1.5.0` 已发布 GitHub Release
> 开工确认：用户「开工」= §7 三项按建议通过；性能门槛按推荐默认处置（见 §7.1），不进 1.6 范围
> 来源：`PLAN-1.5.md` §1 方案 A（原 1.7 候选前移）；版本规则「依次递增、不跳号」不变，最终稳定收口版仍为 `2.0.0`
> 本文同时是**开工前的四条安全不变量评审底稿**；评审未过不得写实现代码

---

## 1. 目标与边界

给本地工具加一条**脚本化 / 计划任务友好**的命令行入口：`AppDataCleaner.exe --scan` / `--dry-run` / `--oneclick`。

- **不改 Web UI、不改清理语义、不新增删除路径**：CLI 只是「同一套服务端能力的第二种调用方式」。
- **不提供** `--auto`（无人值守清理）、`--permanent`（永久删除）、配置写入、多根目录 —— 分别归 1.9 / 既有设计 / 设置页范畴。
- **不联网**：1.6 原定的「更新检查」已取消（见 `PLAN-1.5.md` §1），CLI 全程纯本地。

## 2. 现状核对（规划依据）

| 事实 | 落点 | 对 CLI 的约束 |
|---|---|---|
| 入口只有手写参数 `"--takeover" in sys.argv[1:]`，**无 argparse** | `server.py:373, 862` | 引入 argparse 时必须保留 `--takeover` 语义，且「无任何动作 flag」时启动路径逐字不变 |
| 一键清理单点：`collect_safe_items()` → `run_oneclick(mode)` | `server.py` | CLI 必须复用，禁止另写枚举或删除 |
| 删除动作唯一出口 `core/actions.exec_action(paths, mode, dest, root)`，默认移入回收站、历史 jsonl 可撤销 | `core/actions.py:278`, `core/history.py` | CLI 的 `--oneclick` 沿用默认 recycle + 写历史，不提供 permanent |
| 越界判定 `core/fsutil.inside` + `config.validate` | `core/fsutil.py`, `core/config.py` | **CLI 直调函数绕过 HTTP，但不得绕过校验**——CSRF 令牌是 HTTP 层防护，越界/配置校验是数据层红线 |
| 安全门禁矩阵 G1–G4 已就位 | `tests/test_gate.py` | 1.6 扩为 G1–G6，并入发版前置 |
| onefile exe 入口同为 `server.py`，`--add-data static;static` | `build_exe.bat` | 同一份入口天然支持 `AppDataCleaner.exe --scan`，打包参数不变 |
| 模块级 `CFG` / `STATE` 在 import 时初始化，HTTP 服务 `srv` 在 `main()` 内创建 | `server.py:50, 851` | CLI 分支不创建 `srv`、不开浏览器、不启调度线程、不预热扫描（避免与 CLI 扫描抢 `STATE`） |
| `python server.py` 时模块名是 `__main__`；CLI 若再 `import server` 会**重复加载出第二份 `CFG`/`STATE`** | Python 模块机制 | `cli.py` **不 import server**：由入口用 `cli.run_cli(argv, sys.modules[__name__])` 注入宿主模块，无循环依赖、无重复加载 |

## 3. 功能范围

| must | 设计 |
|---|---|
| **动作 flag（互斥）** | `--scan` 只扫描输出摘要；`--dry-run` 枚举将清理的 safe 项与体积、**零删除**；`--oneclick` 执行一键清理。argparse 互斥组，三者都不给 = 启动 HTTP 服务（行为与 1.5.0 完全相同） |
| **显式确认** | `--oneclick` **必须**同时给 `--yes` 才真删；缺 `--yes` 自动退化为 dry-run 并以非零退出码提示。禁止任何「默认执行删除」 |
| **扫描参数** | `--root`（须通过 `inside(USER_HOME, root)`）、`--max-depth`、`--fresh`（强制全量）、`--hot-mb` / `--big-mb`；缺省取 `DEFAULT_ROOT` 与配置默认 |
| **输出** | 人读格式（中文）+ `--json` 结构化输出（给计划任务/脚本解析）；Windows 控制台编码统一 UTF-8（`sys.stdout.reconfigure`），避免 GBK 乱码 |
| **退出码** | `0` 成功 / `1` 执行失败 / `2` 参数或配置校验错误 / `3` 越界拒绝 / `4` 未完成显式确认（退化 dry-run） |
| **历史与撤销** | `--oneclick` 写同一份 jsonl（mode `cli-oneclick`），默认移入回收站、由 **Windows 回收站还原**；一键清理无 manifest（`undoable=False`），因此**不提供 `undo_move`**（M0 评审修正，见 §9）；不新增存储 |
| **冻结 exe** | `dist/AppDataCleaner.exe --scan / --dry-run / --oneclick --yes` 三种均可跑；无 flag 仍启动服务 |

### CLI 契约（M0 冻结）

- **入口分发**：`server.py` 的 `if __name__ == "__main__"` 先调 `cli.is_cli_invocation(argv)`；命中则 `sys.exit(cli.run_cli(argv, sys.modules[__name__]))`，否则原样 `main()`。`--takeover` 不属于 CLI 参数，单独给仍走服务。
- **模块注入**：CLI 全部服务端能力经注入的 `app` 取用（`app.plan_oneclick` / `app.run_oneclick` / `app.scan.run_scan` / `app.STATE` / `app.inside` / `app.DEFAULT_ROOT` / `app.DATA_DIR` / `app.LOG_DIR`）。
- **枚举单点**：从 `run_oneclick` 抽出 `plan_oneclick()` → `(paths, error)`；`--dry-run` 与真删共用同一份枚举 + level 复核，清单不可能漂移。CLI 只调它，不自己遍历树。
- **同步扫描**：CLI 在当前线程直接 `scan.run_scan(...)`，用 `on_begin/on_finish` 把结果写进 `STATE`；不启后台线程、不开端口。
- **`--json` 统一外层字段**：`version` / `action` / `root` / `ok` / `exitCode` / `error`；按动作附 `scan` / `items` / `results`。单行输出，便于脚本解析。
- **退出码**：`0` 成功 / `1` 执行失败 / `2` 参数或配置校验错误 / `3` 越界拒绝 / `4` 缺 `--yes` 未完成确认。

## 4. 安全不变量评审（开工前必过）

| 门禁 | 1.6 适用性 | 结论 / 新增断言 |
|---|---|---|
| **G1 仅 safe** | CLI 的 `--oneclick` 只能吃 `collect_safe_items()` 的枚举结果 | 沿用；CLI 经 `plan_oneclick()` 取清单，不自己分类 |
| **G2 7 天新鲜度** | 该守门属于**自动清理**；1.6 **不提供 `--auto`**，手动一键清理本就不查新鲜度 | 语义不变、不重开风险 1；`PLAN-1.6` 明确把 `--auto` 列为「不进 1.6」 |
| **G3 默认关闭** | 不带动作 flag 时不新增任何常驻或默认删除行为；CLI 本身是显式一次性调用 | 断言「无 flag 启动路径与 1.5.0 等价」（`is_cli_invocation` 对空 argv / `--takeover` 返回 False） |
| **G4 全部可撤销** | 默认 recycle（系统回收站可还原）+ 写 `cli-oneclick` 历史；不提供永久删除 | 断言历史 mode 为 `cli-oneclick`、exec mode 为 recycle；**修正规划原写「`undo_move` 可还原」——一键清理无 manifest** |
| **新增 G5** | CLI 不得出现第二套删除路径 | 读 `cli.py` 源码，断言不含 `os.remove` / `os.unlink` / `shutil.rmtree` / `force_remove` / `permanent(` / `recycle(` / `exec_action(` |
| **新增 G6** | 防「脚本静默删」 | 断言无 `--yes` 的 `--oneclick` 执行前后目录树、文件数、体积**完全不变**，且不写 `cli-oneclick` 历史 |

**红线**：CLI 不得因为「是本地进程」就跳过 `inside` 越界判定或 `config.validate`；HTTP 的一次性令牌是防 CSRF 的传输层防护，与数据层校验是两回事，绕过前者不等于绕过后者。任何让 CLI 成为「第二套删除路径」的实现都阻断发版。

## 5. 里程碑

| ID | 内容 | 人日 | 依赖 | 出口门槛 |
|---|---|---|---|---|
| **M0** | 评审本文 + 冻结 CLI 契约（flag / 退出码 / `--json` 字段） | 0.3 | — | 四条不变量结论签字；范围不含 `--auto` / `--permanent` |
| **M1** | argparse 入口分支 + `--scan` + `--dry-run` + `--json` / 退出码 / UTF-8 | 1.0 | M0 | 无 flag 启动路径零回归；dry-run 文件系统零变化 |
| **M2** | `--oneclick --yes` + 历史/撤销 + 门禁 G5/G6 + 越界拒绝回归 | 0.8 | M1 | 六条门禁一键可验；缺 `--yes` 必不删 |
| **M3** | 版本 bump 1.6.0、全量回归、脚本版 + exe CLI 双形态冒烟、文档、发版 | 0.6 | M2 | 15 组全绿、双形态冒烟、Release 齐备 |

合计约 **2.7 人日**。

## 6. 验收标准

- 现有 **14 组 491 项**不回归；新增 `tests/test_cli.py`（预计 30+ 项，覆盖 flag 互斥、退出码、`--json` 字段、越界、无 `--yes` 零删除、历史与撤销）。
- `--dry-run` 与无 `--yes` 的 `--oneclick`：被测目录树、文件数、体积前后完全一致。
- `--root C:\Windows\Temp` 被拒（`inside` 生效），退出码 `3`。
- `--oneclick --yes` 默认移入回收站，历史 mode=`cli-oneclick` 可查、结果 `undoable=false`（由系统回收站还原，**非** `undo_move`）。
- 冻结 exe 三种 flag 均可执行；无 flag 启动服务、端口/静态资源/版本号行为与 1.5.0 一致。
- 版本号 6 处字面量统一 `1.6.0`；`CHANGELOG` / `README` / `PLAN-1.5` 矩阵同步；tag `v1.6.0` + Release 附 exe。

## 7. 风险与待确认项

| 风险 | 处置 |
|---|---|
| **CLI 变成第二套删除路径**（本版最大风险） | G5/G6 锁死；实现只允许薄薄一层参数解析 + 调用既有单点，禁止在 CLI 模块写文件操作 |
| `--oneclick --yes` 被写进计划任务后无人复核 | 文档显著位置警告「`--yes` 只对显式调用生效，放进计划任务即等于无人值守删除」；1.6 不提供 `--auto` |
| Windows 控制台中文乱码 / 计划任务无控制台 | 统一 UTF-8 输出；`--json` 供重定向解析；退出码而非文本作为脚本判定依据 |
| onefile exe 每次启动解压开销 | 冒烟记录冷启动耗时；不为了 CLI 改打包形态（onedir 是 2.0 议题） |
| 全局 `STATE` / `CFG` 在「不启 HTTP」时的初始化差异 | M1 回归覆盖：CLI 扫描结果与 Web 扫描结果在同根目录同参数下一致 |

**开工前三项确认（用户「开工」= 全部按建议通过）**：① `--oneclick` 必须 `--yes` —— **确认**；② CLI 只读配置、不提供配置写入 —— **确认**；③ 1.6 不含 `--auto` / `--permanent` / 多根目录 —— **确认**。

### 7.1 性能门槛的默认处置（不进 1.6 范围）

PLAN-1.4 偏差 #9「全量扫描中位数 6.55s > ≤6s 门槛」仍悬而未决。用户「开工」未另行指示，按推荐**默认处置**：接受现状并把基准改为「**≤7s / 同机同比不回退**」，理由是该偏差已量化为基线漂移（+0.8s）+ R4 统计开销（+0.4s），非功能回归；不为它单开性能里程碑、不牵动扫描层。此默认可随时回退；若后续要求硬性达标，须作为独立版本重新排期。1.6 的验收只要求「CLI 扫描不比 Web 扫描慢一档」，不重设门槛。

## 8. 与后续版本的衔接

- 1.7 规则库导入/导出、1.8「申请解锁」引导、1.9 多根目录，仍按 `PLAN-1.5.md` §1 逐个过安全评审。
- 1.6 新增的 G5/G6 并入 `tests/test_gate.py`，成为 **2.0.0 门禁矩阵纳入发版流程** 的一部分；2.0 交付内容不变。
- 更新检查已取消，不因 CLI 的「脚本化」定位而回归。

---

## 9. 偏差记录

| # | 阶段 | 规划口径 | 修正 / 实际 | 原因 |
|---|---|---|---|---|
| 1 | M0 评审 | §3 / G4「`undo_move` 可撤销」 | 改为「默认 recycle + Windows 回收站还原、历史 `cli-oneclick` 可查、`undoable=false`」 | `run_oneclick` 走 `exec_action(paths, "recycle", ...)`，无 manifest；`undo_move` 只适用于 move 模式，原断言不成立 |
| 2 | M0 评审 | 未规定 CLI 如何拿到服务端模块 | 冻结为 `cli.run_cli(argv, sys.modules[__name__])` 注入，`cli.py` 不 import server | `python server.py` 时模块名是 `__main__`，二次 import 会重复加载出第二份 `CFG`/`STATE`/用户规则 |
| 3 | M0 评审 | §4 把 G5 塞在 G1 行里 | 拆为独立 G5（源码禁用删除 API 清单）+ G6（无 `--yes` 零删除），共六条 | 2.0 门禁矩阵要求每条可单独断言 |
| 4 | M2 回归 | §6 预计新增 `test_cli` 30+ 项 | 实际 **56 项**；`test_gate` 30→46 项；全量 **15 组 563 项全绿** | 额外覆盖人读输出、清单一致性、真删历史、清理后幂等 |
| 5 | M2 回归 | 未预见 | 发现并修复 `test_auto` 的时间敏感断言：「未到计划时刻」写死 `hour=14`，15 点后必然失败 | 与 1.6 无关；本次 18 点的全量回归暴露，改为相对当前小时取下一整点，测试不再随钟点漂移 |
| 6 | M3 冒烟 | §6 要求双形态冒烟 | 脚本版全量回归 + UI 0 控制台错误（`logs/ui_check_m16.py`）；冻结 exe 覆盖 `--version` / `--scan` / `--dry-run` / `--oneclick` / `--yes` / 越界 / 无 flag 服务形态（`logs/smoke_m16.py`）全通过 | 同时验证 `sys.modules[__name__]` 注入在 PyInstaller `__main__` 下有效 |
| 7 | M3 收口 | §7.1 性能门槛默认处置 | 基准改为「≤7s / 同机同比不回退」，未牵动扫描层；1.6 未重设门槛 | 与「不进 1.6 范围」一致，可随时回退 |

**验收结论**：六条门禁 G1–G6 一键可验；`--dry-run` 与无 `--yes` 的 `--oneclick` 文件系统零变化；真删只清 safe 级、默认回收站、历史 `cli-oneclick` 可查；冻结 exe 双形态冒烟通过；版本 6 处统一 1.6.0。
