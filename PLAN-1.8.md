# PLAN-1.8 — 「申请解锁」引导（protected 项一键跳转 Windows 权限说明）

> 状态：**M0 定稿**（2026-11-11）· 前置 `v1.7.0` 已发布 GitHub Release
> 承接：PLAN-1.5 方案 A 版本矩阵 —— 1.8.0 = 「申请解锁」引导（原 1.8 拆出的轻量项）。

## 1. 目标与范围

protected 项（系统/程序数据）在 UI 中只能看到「禁止删除」的灰字提示，用户不知道
下一步能做什么。本版给 protected 项一个 **解锁引导** 入口：解释为什么被保护、
给出官方推荐的手动操作步骤、一键打开微软官方说明页。

**铁律：本版不提供任何解锁/删除能力本身。** 不执行 takeown/icacls，不修改任何
文件 ACL，不新增任何写路径——只做「解释 + 跳转官方文档」。

| in scope | out of scope（记录不承诺） |
|---|---|
| `POST /api/unlock-guide`：服务端复核 protected 并下发引导内容 | 执行任何提权/解锁命令（takeown、icacls 一律不进代码） |
| 目录树 protected 行「引导」按钮 + 预览弹窗 blocked 提示区入口 | ms-settings 深链（文件 ACL 无对应系统设置页，官方文档页为准） |
| 引导弹窗（步骤 + 白名单官方链接 + 免责说明） | 一键解锁按钮、ACL 修改、自动提权重启 |
| 引导内容测试 + G1–G6 门禁复验 | 旧版 Windows 专属步骤分支（统一给通用 NTFS 步骤） |

## 2. 引导内容设计

- **服务端单点下发**（延续「前端不保存逻辑」）：新建 `core/guide.py`，
  内容 = 通用步骤 + 规则相关说明（来自 `classify(path)` 的 rule/vendor/note）+ 官方链接白名单。
- **通用步骤**（Win10/11 NTFS 通用）：
  1. 在文件资源管理器中右键该目录 →「属性」→「安全」选项卡；
  2. 若无法修改，点「高级」→「所有者」处「更改」，将所有者改为当前用户或 Administrators；
  3. 勾选「替换子容器和对象的所有者」递归应用；
  4. 回到「安全」选项卡给当前用户授予所需权限；
  5. 完成后重新扫描即可看到分类更新。
  步骤末尾固定免责：「请先确认目录确实不再被系统或对应程序需要；本工具不代为修改任何权限。」
- **官方链接白名单**（硬编码于 `core/guide.py`，前端只渲染后端返回的 URL，不自行拼接）：
  - `https://support.microsoft.com/zh-cn/kb/308421` — 获取文件或文件夹的所有权
  - `https://learn.microsoft.com/zh-cn/windows-server/administration/windows-commands/takeown` — takeown 命令参考
  - `https://learn.microsoft.com/zh-cn/windows-server/administration/windows-commands/icacls` — icacls 命令参考
- **规则相关说明**：命中内置保护规则时返回该规则的 vendor/note（如「Edge、Office、
  商店组件的程序数据…」）；命中用户自定义 protected 规则时返回 note 并标注（自定义规则）。

## 3. 设计决策（已定）

1. **服务端复核，不信任前端**：`POST /api/unlock-guide {path}` 里 `classify(path)`
   的 level 必须 `protected` 才返回 `protected: true` + 引导内容；否则返回
   `protected: false`（200，非错误——该项本来就不可删，引导无意义）。
2. **不做文件系统探测**：引导针对「可能没有读取权限」的路径，`os.stat` 可能正好因
   权限失败。只做字符串规则复核，绝不触碰磁盘。path 仅用于 `path_parts` + trie 匹配。
3. **path 校验**：必须是字符串、≤4096 字符；非字符串/超长 → 400。不设其他格式限制
   （`path_parts` 本身容错）。
4. **通用与定制两用**：`path` 可省略 → 返回通用引导（预览弹窗入口用）；带 path →
   附加规则说明（目录行入口用）。
5. **前端跳转安全**：`window.open(url, "_blank", "noopener")`；URL 只来自后端白名单。

## 4. 安全评审（G1–G6 门禁）

| 门禁 | 本版影响 | 结论 |
|---|---|---|
| G1 仅 safe | 不触碰清理清单；引导入口只出现在 deletable=false 的行 | 不破坏 |
| G2 7 天新鲜度 | 不涉及自动清理 | 不破坏 |
| G3 默认关闭 | 不涉及 autoClean | 不破坏 |
| G4 全部可撤销 | 不执行任何删除/写操作 | 不破坏 |
| G5 CLI 源码零删除 API | 不改 `cli.py` | 不破坏 |
| G6 无 `--yes` 零执行 | 不改执行路径 | 不破坏 |

新增攻击面评估：新端点只读 `classify`（纯字符串匹配），无 IO、无子进程、无 eval；
`core/guide.py` 链接白名单硬编码；请求体走既有 1MB 上限。**零新增执行面。**

## 5. API 契约

- `POST /api/unlock-guide`，body `{"path": "C:\\...\\Local\\Microsoft"}`（path 可省略）
  - 命中保护：`200 {"protected": true, "rule": "local/microsoft", "vendor": "Microsoft",
    "note": "...", "custom": false, "steps": [...], "links": [{"title","url"},...], "disclaimer": "..."}`
  - 未命中保护（或省略 path）：`200 {"protected": false, "steps": [...], "links": [...],
    "disclaimer": "..."}`（通用引导）
  - path 非字符串/超长：`400 {"error": "..."}`
- 前端：目录树 protected 行（复选框 disabled 的行）末列加 **「引导」** 按钮；
  预览弹窗 blockedCount 警示语追加 **「查看解锁引导」** 链接；两者都打开新引导弹窗
  （遮罩 + 卡片，沿用预览弹窗结构），弹窗内「打开官方说明页」逐条列出白名单链接。

## 6. 里程碑

| ID | 内容 | 出口门槛 |
|---|---|---|
| M0 | 本文档：契约冻结 + 安全评审 | §2/§3/§5 定稿 |
| M1 | `core/guide.py` + 端点 + `tests/test_guide.py`；顺手清理 `rulepkg.py` 未用导入（ruff F401） | 新测试全绿；G1–G6 复验通过 |
| M2 | UI 接入（行按钮 + 预览入口 + 弹窗）+ 无头 UI 核验 | 0 控制台错误；链接逐条可点 |
| M3 | bump 1.8.0（6 处）、CHANGELOG、README、16 组回归、冻结冒烟、tag `v1.8.0`、GitHub Release | 与 1.7 发版清单逐项对齐 |

## 7. 验收标准

- protected 路径（内置 + 用户自定义）返回 `protected:true` + 规则说明；safe/data 路径
  返回 `protected:false`；省略 path 走通用引导。
- 引导内容含 ≥3 个官方链接且 URL 全部在白名单内（测试断言 url 前缀）。
- 端点零 IO：mock 不了也无妨——代码评审确认无 os/subprocess/shutil 调用（可加 G 项静态断言）。
- 16 组回归全绿不回归；UI 0 控制台错误。

## 8. 偏差记录（实施回写）

| # | 阶段 | 偏差 | 处置 | 影响 |
|---|---|---|---|---|
| 1 | M0 前 | 用户询问是否引入「2026 新技术」（uv/Ruff/Python 3.14 自由线程等） | 决策：**技术栈冻结在纯标准库**——运行时零依赖是安全工具的审计面卖点；2026 热点（uv 工具链、No-GIL、t-strings）均不对症。仅做一次 `uvx ruff check` 只读体检：284 条中无真 bug，产品代码唯一问题为 `rulepkg.py` 未用导入（F401），M1 顺手清理 | 无 |
| 2 | M1 测试 | 零执行面静态断言误伤 `guide.py` docstring 中「os.stat」字样 | 改 docstring 措辞为「stat 类调用」；断言保留（该断言是本版新增的长期门禁资产） | 无 |
| 3 | M1 测试 | 初版 test_api 恢复配置只发 `{"rules": ...}`，会把 port/dest/autoClean 重置为默认 | 改为 test_rulepkg 同款「整份配置快照 + finally 恢复」模式 | 无 |
| 4 | M2 UI 核验 | protected 行默认不出现在目录排行：`HOT_FLOOR=10MB` + 「单子目录占 75% 不进榜」两条既有规则把 12MB 的 `Local\Microsoft`（100% 被 Edge 支配）挡在榜外 | 核验脚本改为给 `Local\Microsoft` 造两个 6MB 子目录（12MB 过地板、50% 不被支配）；**产品行为正确无需改动**，此为测试夹具问题 | 无 |
| 5 | M2 UI 核验 | 预览弹窗「查看解锁引导」入口仅在 blockedCount>0 时渲染，而 protected 行复选框本身禁用、常规操作流选不进预览——无法无头触发 | 改为注入带 `data-guide` 的测试锚点验证共享委托处理器（同一 `openGuide` 路径）；入口代码保留，真实触发场景 = 用户自定义 safe 规则与内置保护冲突等边缘态 | 无 |
| 6 | M3 | 无 | 17 组 643 项全绿；UI 核验 0 控制台错误；冻结冒烟四态通过 | — |

收口：**16 组既有回归 + 1 组新增 = 17 组 643 项全绿**；`logs/ui_check_m18.py` 通过
（引导按钮 / 弹窗内容 / 3 官方链接 / 委托处理器 / 0 控制台错误，截图 `logs/m18-ui.png`）；
`logs/smoke_m18.py` 冻结冒烟通过（版本 1.8.0 / 引导四态 / 1.7 规则包不回退）。
