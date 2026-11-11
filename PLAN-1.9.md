# PLAN-1.9 — 多根目录管理（根注册表）

> 状态：**M0 定稿**（2026-11-11）· 前置 `v1.8.0` 已发布 GitHub Release
> 承接：PLAN-1.5 方案 A 版本矩阵 —— 1.9.0 = 多根目录管理；2.0.0 为稳定收口版。

## 1. 目标与范围

让用户可以登记多个常用扫描根（下载目录、开发缓存等），扫描时一键切换，
**而不是每次手敲路径**。本版只做「登记 + 校验 + 切换」，不扩张删除面。

| in scope | out of scope（记录不承诺） |
|---|---|
| `config.roots`：登记根列表（最多 8 个），持久化 + 启动容错 | 跨盘根 / 用户目录之外的根（见 §3 决策 1，待用户确认） |
| `core/roots.py`：根校验单点（系统目录黑名单/盘符根/穿越/嵌套/重复） | 多根并行同时扫描（仍一次只扫一个根，STATE 单实例） |
| `GET /api/roots`、`POST /api/roots/add`、`POST /api/roots/remove` | 根目录 ACL 检测、盘符可用空间多根汇总 |
| UI：扫描根 datalist 下拉 + 管理弹窗（增删、默认根标识） | 按根隔离的自动清理策略（自动清理仍按「当前扫描结果」单点） |

## 2. 兼容性契约（本版最高约束）

用户明确要求「不破坏现有行为和接口兼容性」，据此冻结以下四条：

1. **`/api/scan` 一行不改**：仍 `inside(USER_HOME, root)` + `isdir`，
   **不强制要求 root 已登记**。旧客户端、CLI、既有 16 组回归传任意 USER_HOME
   内路径（如 `testdata/`）照常扫描。
2. **`config.validate` 对旧配置无感**：缺 `roots` → 默认 `[]`（空表 = 只有内置
   默认根 AppData）；旧 config.json 启动自动补字段，不报 warning、不阻塞启动。
3. **`POST /api/config` 不带 `roots` 时保留现值**（端点层兜底），旧客户端保存
   设置不会把已登记根清空。带 `roots` 时走同一 `_validate_roots` 校验单点。
4. **所有新增接口零删除/执行面**：不碰 `actions`、不碰 `exec_action`、不碰 CLI。
   G1–G6 门禁不变量保持（自动清理仍取 `STATE.root()` 当前扫描结果 + 7 天新鲜度）。

## 3. 设计决策（已定）

1. **信任边界不放宽**：登记根必须落在 `USER_HOME` 内——与 `/api/scan` 既有
   `inside(USER_HOME, root)` 完全同一条线。用户的「下载目录 / 开发缓存」
   绝大多数就在用户目录内；跨盘（如 `D:\Downloads`）需要重新论证边界，
   **列为本版待用户确认项，不擅自放开**。
2. **注册表 ≠ 白名单强制**：登记是 UX 便利（下拉切换），不是新的安全闸门；
   真正的安全闸门仍是 `/api/scan` 既有校验。理由：若强制「必须已登记」，
   等于给旧客户端加新拒绝条件，直接违反 §2.1。
3. **默认根内置、不可删**：`DEFAULT_ROOT = USER_HOME\AppData` 由 `/api/roots`
   合成返回（`default: true`），不写进 config.roots，保证配置永远有一个兜底根。
4. **拒绝嵌套根**：新根不能是已有根（含默认根）的父或子目录，避免列表里出现
   `AppData` 与 `AppData\Local` 这种语义重叠。也拒绝重复（normpath 小写比较）。
5. **系统目录黑名单**：路径段命中 `windows` / `program files` / `program files (x86)`
   / `programdata` / `system volume information` / `$recycle.bin` 一律拒绝；
   另用 `WINDIR`/`ProgramFiles`/`SystemRoot` 环境变量做二次拦截。
6. **label 纯文本**：去控制字符、≤32 字符、默认取目录名；前端渲染一律 `esc()`，
   后端不做 HTML 拼接。
7. **持久化单点**：所有改动经 `config.save`（自动 `.bak`）后更新 `CFG`，与
   1.7 规则包导入同款顺序：先校验 → 再落盘 → 再更新内存。

## 3. 新增 API 契约

- `GET /api/roots` → `200 {"roots": [{"path","label","default"}], "defaultRoot",
  "userHome", "max": 8, "current"}`（默认根恒排第一）
- `POST /api/roots/add` `{path, label?}` → `200 {"ok": true, "roots": [...]}`；
  非法/重复/超限/嵌套 → `400 {"error": "..."}`
- `POST /api/roots/remove` `{path}` → `200 {"ok": true, "roots": [...]}`；
  默认根或不存在 → `400`
- `/api/config` GET 自然带 `roots` 字段；POST 不带时保留现值（§2.3）。

## 4. 安全评审（G1–G6）

| 门禁 | 影响 | 结论 |
|---|---|---|
| G1 仅 safe | 登记根不参与分类；扫描根变化时规则判定仍由 `classify()` 单点 | 不破坏 |
| G2 7 天新鲜度 | auto-clean 不动；仍读当前 STATE 结果 | 不破坏 |
| G3 默认关闭 | `validate` 不触碰 autoClean | 不破坏 |
| G4 全部可撤销 | 无删除动作；config.save 有 .bak | 不破坏 |
| G5 CLI 零删除 API | 不改 cli.py | 不破坏 |
| G6 无 --yes 零执行 | 不改执行路径 | 不破坏 |

新增面评估：`core/roots.py` 只做字符串规范化 + `os.path.isdir` 存在性检查
（只读 stat，不递归、不写）；端点只改 config.json。零新增删除面。

## 5. 里程碑与验收

| ID | 内容 | 出口门槛 |
|---|---|---|
| M0 | 本文档：契约冻结 + 安全评审 | §2/§3 定稿 |
| M1 | `core/roots.py` + config.roots + 三个端点 | 校验矩阵单测通过；/api/scan 无改动 |
| M2 | UI（datalist + 管理弹窗）+ `tests/test_roots.py` | 18 组全绿；旧配置兼容专项 |
| M3 | bump 1.9.0（6 处）、文档、回归、UI 核验、冻结冒烟、tag、Release | 与 1.8 发版清单逐项对齐 |

验收标准：
- 合法根可增删持久化；默认根不可删；重复/嵌套/系统目录/盘符根/相对路径/超 8 个全部 400。
- 旧 config（无 roots 字段）启动后 `roots == []` 且服务可起；`POST /api/config`
  不带 roots 不清空已登记根。
- `/api/scan` 传未登记的 USER_HOME 内路径仍 200（回归组自带验证）。
- 18 组回归全绿；UI 0 控制台错误；冻结 exe 冒烟含根列表 API。

## 6. 偏差记录（实施回写）

| # | 阶段 | 偏差 | 处置 | 影响 |
|---|---|---|---|---|
| 1 | M0 | 用户要求「不破坏现有行为和接口兼容性」列为最高约束，原候选池「AppData 之外自定义**任意**根」会与既有 `inside(USER_HOME, root)` 冲突 | 注册表限定在 USER_HOME 内；**不强制 /api/scan 校验登记**；跨盘根列为待用户确认项，不擅自放宽 | 功能覆盖下载目录/开发缓存等主流场景；跨盘需求待确认 |
| 2 | M1 | `config.validate` 若把坏根当硬错误，会导致「登记根指向临时不可用目录 → 整个设置页保存失败」 | 坏根降级为 warnings（丢弃该项、整体仍可保存），与 rules 的硬错误语义不同；`/api/roots/add` 自己先做严格 `validate_item` 保证新根存在 | 无，兼容性更好 |
| 3 | M1 | `core/roots.py` 需复用 home 边界但不宜反向依赖 config/server | 模块内实现同语义 `_inside_home`（纯字符串），校验单点仍在 roots.py；`DEFAULT_ROOT` 由 `default_root(home)` 合成，server 既有常量一行未改 | 无 |
| 4 | M2 | 管理弹窗删除登记根后，扫描输入框仍保留该路径 | 保留（设计如此）：/api/scan 不强制登记，手动输入的合法路径仍可扫，不擅自清空用户输入 | 无 |
| 5 | M2 | UI 核验需真实增删配置，中途失败会污染 config.json | 脚本 finally 兜底调用 `/api/roots/remove`；成功路径本身已删除 | 无 |
| 6 | 测试 | 新组会触发一次扫描，放回归序列中部会污染其他组的 STATE 结果 | `test_roots` 放在 18 组序列**最后**（CLI 组之后） | 无 |

收口：**18 组 692 项全绿**（新增 49 项）；`logs/ui_check_m19.py` 通过
（下拉/弹窗/添加/切换/删除/0 控制台错误，截图 `logs/m19-ui.png`）；
`logs/smoke_m19.py` 冻结冒烟通过（根 API 闭环 + 旧客户端配置兼容 + 1.8 引导不回退）。
