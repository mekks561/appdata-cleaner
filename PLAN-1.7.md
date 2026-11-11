# PLAN-1.7 — 规则库导入/导出（JSON 规则包）

> 状态：**已交付**（2026-11-11，M0–M3 完成）· 前置 `v1.6.0` 已发布 GitHub Release
> 承接：PLAN-1.5 方案 A 版本矩阵 —— 1.7.0 = 规则库导入/导出（原 1.8 拆出）。

## 1. 目标与范围

让用户能把自定义规则打包带走、换机器恢复、或分享给他人，**不改变任何判定语义**。

| in scope | out of scope（记录不承诺） |
|---|---|
| 导出规则包（浏览器下载 JSON 文件） | CLI 的 `--export-rules` / `--import-rules`（规则包是配置面，不是清理面；避免扩大 CLI 删除面评审范围） |
| 导入规则包（文件选择 → 校验 → 替换生效） | 规则编辑 UI 重做（仍是现有 textarea + 保存） |
| 包格式校验矩阵与安全评审 | 云同步、多规则库管理（1.9 多根目录同批搁置） |
| 往返一致性测试 + 门禁测试 | 合并式导入（见 §3 决策 2） |

## 2. 包格式（v1）

```json
{
  "format": "appdata-cleaner-rules",
  "version": 1,
  "exportedAt": "2026-11-11T09:30:00",
  "appVersion": "1.6.0",
  "count": 3,
  "rules": [
    {"segments": ["local", "mytool", "cache"], "level": "safe",
     "note": "自建工具的下载缓存，删除后自动重建", "vendor": "自定义"}
  ]
}
```

- `rules` 数组的每一条与 `config.json` 的 `rules` 字段**同构**，复用 `config._validate_rules` 校验单点（MAX_RULES=64、MAX_SEGMENTS=8、SEG_RE、note 必填）。
- `format` / `version` 是身份字段：不匹配 → 整包拒绝（400），不做静默兼容；未来格式演进走 `version` 递增。
- 未知名段一律忽略（向前兼容），不做代码执行——只用 `json` 解析。

## 3. 设计决策（已定）

1. **导入 = 整体替换**。与现有设置页「保存设置」发送完整 rules 数组的语义一致，无合并歧义（重复段怎么算、优先级谁赢都不需要新定义）；UI 明示「导入将替换当前全部自定义规则」。合并式导入留候选池。
2. **校验单点复用**。导入的每条规则走 `config._validate_rules`，与手输 textarea、`/api/config` 完全同一条路径，不出现第二套规则校验。
3. **坏条目降级为警告**（与现有语义一致）：整包格式对、个别条目非法 → 合法条目生效、非法条目逐条报告；整包格式错（缺 format/version 错/rules 非数组/超 64 条）→ 整包拒绝、现规则原样不动。
4. **先验证后落盘**：任何错误都在 `config.save`（自动 `.bak` 备份）之前发生，绝不写半份配置。

## 4. 安全评审（四条不变量 + 1.6 新增两条门禁）

| 不变量 | 本版影响 | 结论 |
|---|---|---|
| G1 仅 safe | 导入的 safe 规则进入 `_USER_SOFT_TRIE`；`classify()` 优先级（用户 protected > 内置 protected > 用户 safe）不变，**导入 safe 无法把 `Local\Microsoft` 等内置保护目录标成可删** | 不破坏；新增专项测试 |
| G2 7 天新鲜度 | 自动清理守门不涉及 | 不破坏 |
| G3 默认关闭 | 规则导入不触碰 `autoClean`；配置其余段在替换 rules 时原样保留 | 不破坏；测试覆盖「导入只动 rules」 |
| G4 全部可撤销 | 规则导入不执行任何删除；`config.save` 有 `.bak` 回退 | 不破坏 |
| G5（1.6）CLI 源码零删除调用 | 本版不改 `cli.py` | 不破坏 |
| G6（1.6）无 `--yes` 零删除 | 本版不改执行路径 | 不破坏 |

包解析防御性约束：请求体走既有长度上限；`validate_package` 只接受 dict；`count` 与实际条数不符仅警告不拒绝（防止手改包时数错）；导出文件名固定前缀，不含用户输入。

## 5. API 契约

- `GET /api/rules/export` → `200`，`Content-Type: application/json`，
  `Content-Disposition: attachment; filename="appdata-cleaner-rules-YYYYMMDD.json"`，body 即包格式。无自定义规则时导出空 rules 包（合法，导入等于清空）。
- `POST /api/rules/import`，body = 包 JSON →
  - 整包合法：`200 {ok, applied, dropped, warnings, customRules}`
  - 整包格式错：`400 {error, details[]}`，现配置不动
- 前端：设置页「自定义规则」区新增 **导出规则包**（`<a download>` 指向 export）与 **导入规则包**（file input → FileReader → JSON.parse 仅作 UX 预检 → POST）；导入成功后重新拉取 `/api/config` 刷新 textarea 并展示 applied/dropped。

## 6. 里程碑

| ID | 内容 | 出口门槛 |
|---|---|---|
| M0 | 本文档：契约冻结 + 安全评审 | §2/§3/§5 定稿 |
| M1 | `core/rulepkg.py` + 两个端点 | 包校验矩阵单测通过；导入替换 + 持久化正确 |
| M2 | UI 接入 + `tests/test_rulepkg.py` | 15+ 组全绿；提升内置保护专项断言 |
| M3 | bump 1.7.0、文档、回归、exe、发版 | 6 处版本字面量一致；冻结冒烟含导出/导入；Release 齐备 |

## 7. 验收标准

- 导出→清空→导入，规则逐条还原；`/api/info` customRules 计数一致。
- 恶意/坏包全部拒绝或降级：缺 format、version=2、format 名错、rules 非数组、超 64 条、段含 `..`、level 非法、note 缺失。
- 导入 `local/microsoft | safe` 后 `classify` 仍判 protected（G1 专项）。
- 导入只改 rules：port/dest/autoClean/warnGB/dup 原样。
- 15 组既有测试不回归；新增组全绿；UI 0 控制台错误。

## 8. 偏差记录（实施回写）

| # | 阶段 | 偏差 | 处置 | 影响 |
|---|---|---|---|---|
| 1 | 实施前 | §3 决策 1 原述「合并式导入留候选池」未给理由 | 已在 CHANGELOG 补理由：与既有「保存设置」整体替换语义一致，无合并歧义（重复段优先级无需新定义） | 无 |
| 2 | 测试 | 初版断言两处写错（`valid=[]` 真值误用；对未注入规则的目录期望 safe，实际命中启发式 suspect） | 修正测试本身，非产品缺陷；提权专项改为双规则（提权失败 + 普通 safe 生效） | 无 |
| 3 | 范围确认 | 段名含点号（如 `bad..seg`）实际是 SEG_RE 允许的合法段——按段精确匹配无穿越风险 | 保留现状；测试改用真正非法的 `../evil` 段做丢弃断言 | 无 |
| 4 | M3 | 导出文件名由服务端按日期生成，前端 `<a download>` 的 download 属性名被服务端 Content-Disposition 覆盖（规范行为） | 无需处理；文件名即预期格式 | 无 |

收口：16 组 607 项全绿；UI 核验（导出/导入按钮接线 + 0 控制台错误）；冻结 exe 冒烟含导出/导入 API；Release `v1.7.0` 发布。
