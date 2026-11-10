# PLAN-1.6 — CLI 模式（方案 A 顺延后的 1.6.0）

> 状态：**规划稿 / 待开工确认**（2026-11-10）· 前置 `v1.5.0` 已发布 GitHub Release
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

## 3. 功能范围

| must | 设计 |
|---|---|
| **动作 flag（互斥）** | `--scan` 只扫描输出摘要；`--dry-run` 枚举将清理的 safe 项与体积、**零删除**；`--oneclick` 执行一键清理。argparse 互斥组，三者都不给 = 启动 HTTP 服务（行为与 1.5.0 完全相同） |
| **显式确认** | `--oneclick` **必须**同时给 `--yes` 才真删；缺 `--yes` 自动退化为 dry-run 并以非零退出码提示。禁止任何「默认执行删除」 |
| **扫描参数** | `--root`（须通过 `inside(USER_HOME, root)`）、`--max-depth`、`--fresh`（强制全量）、`--hot-mb` / `--big-mb`；缺省取 `DEFAULT_ROOT` 与配置默认 |
| **输出** | 人读格式（中文）+ `--json` 结构化输出（给计划任务/脚本解析）；Windows 控制台编码统一 UTF-8（`sys.stdout.reconfigure`），避免 GBK 乱码 |
| **退出码** | `0` 成功 / `1` 执行失败 / `2` 参数或配置校验错误 / `3` 越界拒绝 / `4` 未完成显式确认（退化 dry-run） |
| **历史与撤销** | `--oneclick` 写同一份 jsonl（mode 用 `cli-oneclick` 与 `auto-oneclick` / 手动区分），可用既有 `undo_move` 撤销；不新增存储 |
| **冻结 exe** | `dist/AppDataCleaner.exe --scan / --dry-run / --oneclick --yes` 三种均可跑；无 flag 仍启动服务 |

## 4. 安全不变量评审（开工前必过）

| 门禁 | 1.6 适用性 | 结论 / 新增断言 |
|---|---|---|
| **G1 仅 safe** | CLI 的 `--oneclick` 只能吃 `collect_safe_items()` 的枚举结果 | **扩展 G5**：CLI 入口源码不含 `os.remove` / `os.unlink` / `shutil.rmtree` / `force_remove` 调用；删除只能经 `exec_action` |
| **G2 7 天新鲜度** | 该守门属于**自动清理**；1.6 **不提供 `--auto`**，手动一键清理本就不查新鲜度 | 语义不变、不重开风险 1；`PLAN-1.6` 明确把 `--auto` 列为「不进 1.6」 |
| **G3 默认关闭** | 不带动作 flag 时不新增任何常驻或默认删除行为；CLI 本身是显式一次性调用 | 断言「无 flag 启动路径与 1.5.0 字节级等价」（回归覆盖） |
| **G4 全部可撤销** | 默认 recycle + 写历史；不提供永久删除 | 断言 `--oneclick` 历史 mode 为 `cli-oneclick` 且 `undo_move` 可还原 |
| **新增 G6** | 防「脚本静默删」 | 断言无 `--yes` 的 `--oneclick` 执行前后目录树与文件数**完全不变**（含 0 次删除调用） |

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
- `--oneclick --yes` 默认移入回收站，历史可查、`undo_move` 可还原。
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

**开工前需你确认三件事**：① `--oneclick` 是否接受「必须 `--yes`」（建议是）；② CLI 是否只读配置、不提供配置写入（建议是）；③ 是否确认 1.6 不含 `--auto` / `--permanent` / 多根目录（建议是）。三项默认按「建议」执行，如无异议可直接开工。

## 8. 与后续版本的衔接

- 1.7 规则库导入/导出、1.8「申请解锁」引导、1.9 多根目录，仍按 `PLAN-1.5.md` §1 逐个过安全评审。
- 1.6 新增的 G5/G6 并入 `tests/test_gate.py`，成为 **2.0.0 门禁矩阵纳入发版流程** 的一部分；2.0 交付内容不变。
- 更新检查已取消，不因 CLI 的「脚本化」定位而回归。

---

## 9. 偏差记录

（待实施时回写）
