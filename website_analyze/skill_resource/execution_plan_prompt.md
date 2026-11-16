# GEO 优化执行清单 — 撰写规范

诊断报告回答「问题是什么」，执行清单回答「今天该做哪几件事」。本文件给 agent 直写执行清单用（不经渲染层）。

> 本文件是仓库 `SKILL.md` Phase 2.5 的自包含版本。

## 数据来源（只能从这里取，禁止重新诊断）

- `scores.json` 的 `findings` / `quick_wins` / `medium_term` / `strategic`
- `site_analyze` 返回的 `p0_blockers`（P0 阻塞清单；**仅 `confidence` 为 `deterministic` 的项参与 P0 定级**，`inferred` 疑似项单独列出不定 P0）
- `diagnose_run` / `site_rescan` / `site_analyze` 的实测值（HTTP 状态、词数、URL、爬虫名单、sameAs 缺失项），用于填充 `evidence` 与代码块

## 任务提取规则

- **合并**：同一根因引发的多个 finding 合并为一个任务（GPTBot 被拦 + ClaudeBot 被拦 + robots 规则错误 → 「修复 robots.txt 的 AI 爬虫访问规则」）。
- **拆分**：一条建议若含多个可独立完成/验证的工作则拆开；拆出来的每个任务都能独立勾选、独立验证。
- **数量预期**：总任务数控制在 **8–14 项**。少于 8 聚合过度，多于 14 拆得过碎。
- **页面级聚合**：同一页面同类改动合并为一个任务（子项列出），判断依据是共享「涉及页面 + 负责人 + 一次发布」。
- **实体级聚合**：同一实体多字段（Organization 的 name/logo/sameAs/address/contactPoint）必须合并；站点级实体（Organization+WebSite）通常也合并。
- **阶段级聚合**：外部品牌建设按「见效快慢」拆两段——P1 认领已有平台公司页（LinkedIn/YouTube/Crunchbase/Trustpilot）；P3 建 Wikisource/百科词条（需积累媒体报道）。
- **不拆红线**：拆分会导致多个任务「涉及页面 + 前置依赖 + 负责人」完全相同时，不该拆。

## 优先级判定

findings 的 `severity` 与任务 `priority` 是同一条轴上的两套词，映射固定，禁止自行发明对应关系：

| finding severity | 任务 priority | 含义 |
|---|---|---|
| Critical（且来自 `p0_blockers` 的 **deterministic** 项） | **P0** | 阻塞：不修则其余优化不生效 |
| Critical（非阻塞类，或来自 `p0_blockers` 的 **inferred** 疑似项） / High | **P1** | 对核心 GEO 能力明显有帮助 |
| Medium | **P2** | 优化项，有影响但可延后 |
| Low | **P3** | 低影响、长期建设 |

| 级别 | 触发条件 |
|---|---|
| P0 | 对应 site_analyze 返回 `p0_blockers` 里 `confidence` 为 **deterministic** 的阻塞项（五类 GEO 阻塞，服务端确定性判定；`inferred` 疑似项不定 P0，列 P1 并注明「疑似，复测后确认」）——**修复成本不影响是否进 P0**，只影响 P0 内部的排期顺序；理想 3–5 项，超 6 项说明有的其实是 P1 |
| P1 | 对核心 GEO 能力明显有帮助：品牌实体（Organization schema、sameAs、平台官方账号认领与回链）、Schema 关键类型、核心产品/服务页 |
| P2 | 内容增强、FAQ、页面细节、引用信号、辅助内容 |
| P3 | 低影响、长期建设（Wikidata/Wikipedia 词条、持续 Digital PR）、非核心页面 |

**P0 双向对齐（硬性）**：
- 正向：每个 P0 任务都必须对应 `p0_blockers` 里的一条阻塞项（原有规则）。
- **反向：`p0_blockers` 里每条 blocker 都必须在执行清单里有一条对应的 P0 任务**，一条都不能漏。若某 blocker 修复成本高（如需数周），仍列 P0，仅在排期上后置并注明耗时——**阻塞性质与修复成本无关**，禁止因成本高降级到 P1/P2 或从清单中省略。
- 同理反向覆盖：**每条 Critical finding 都必须在清单中有对应任务**（P0 或 P1）；确因只能进长期建设（如需数月的词条/媒体积累）而无法列入的，必须在 scores.json 的 `impact_estimate` 里显式说明过的那一类，清单里也要以 P3 任务形式保留占位并注明「长期，原因见报告 impact_estimate」——**不允许无声消失**。

**P0 红线**：Critical 是「通路/可信度」问题，不是「内容质量」问题。修复成本只影响排序、不影响定级——Wikidata 词条、媒体报道积累即使影响大也绝不进 P0。**「有实质正文但可引用性低」（如 871 词 0 数字、段落全 D 级）是内容质量问题，不是阻塞**——阻塞让 AI「读不到」，低可引用性只是「不想引」；此类任务最高 P1。

## 任务字段（每个任务必填）

```text
task_id          TASK-001 递增
title            动作 + 对象（不写"优化 XX"虚词）
priority         P0 / P1 / P2 / P3
category         技术基础 / Schema 与实体 / 内容与可引用性 / 品牌外部 / 平台与爬虫
estimated_time   具体到分钟/小时（"10 分钟"/"1–2 小时"），禁用"较短/适中/较长"
difficulty       简单 / 中等 / 较复杂
dependencies     前置 task_id 列表，无则写「无」
why              解决什么问题（引用诊断 finding）
steps            可执行步骤（1/2/3…）
deliverable      产出物（哪个文件、字段、页面）
verification     完成标准 + 验证方式
status           未开始 / 进行中 / 待验收 / 已完成
```

## 排序逻辑

综合 优先级 × 耗时 × 依赖 × 难度，输出真实可执行顺序：P0 里 5 分钟排前、半天排后；补 sameAs 依赖 Organization 存在；作者冲突修复先于内容改写。

## 禁止事项

- 不重复诊断结论（不重新打分、不复制 findings 大段正文）
- 不做收益承诺（禁写「完成后 GEO Score 提升 N 分」，只写「完成后建议重新诊断」）
- 不用 Emoji 作主要信息结构；不用 Mermaid/HTML；任务/验收清单统一 `- [ ]` checkbox，任务状态四态「未开始 / 进行中 / 待验收 / 已完成」

## Markdown 骨架（飞书交付版）

```markdown
# {品牌} GEO 优化执行清单｜基于 {YYYY-MM-DD} 诊断报告
> 诊断对象：{url}｜报告日期：{date}｜采样范围：{...}｜文档版本：vX.Y
> ❗ 真实性红线：成立时间/地址/用户数/覆盖国家/响应速度/产品参数/保修/媒体评分/作者身份/社交账号必须有可核验来源，无法确认应删除字段，不得发布占位值或虚构。
> ❗ 前置判定（阻塞性检查）：**只读 site_analyze 返回的 `p0_blockers`**——它是服务端按五类 GEO 阻塞从实测数据算出的清单，与诊断报告的 P0 前置判定同源。**只把 `confidence` 为 `deterministic`（或未标 confidence）的 blocker 当作阻塞**：此类非空时**必须写「存在阻塞性内容，需先完成 P0 调整，完成后方可进行 GEO 优化」**，并逐条列出 blocker 标题；`confidence` 为 `inferred` 的疑似项单独列为「疑似阻塞，建议复测确认」，不定 P0、不阻断动工；全部为空或全为 inferred 时**必须写「可直接动工」**。禁止自行把 findings 解释为阻塞或解除阻塞，保证与报告判定永远一致。**修复耗时不改变阻塞性质**——「1 个工作日内可完成」的阻塞项仍是阻塞，耗时只影响排期顺序。

## 一、诊断基线与优化重点（五维基线表格 + 豁免说明）
## 二、开始前资料准备清单（checkbox + 资料存放链接）
## 三、优先级与建议排期（P0/P1/P2/P3 + 维护表格）
## 四、逐项执行与验收清单（每个任务：元信息表 + 诊断依据 + 涉及页面 + 执行清单 + 代码块 + 验收清单）
```

- 「可直接使用」代码块：涉及 robots.txt/llms.txt/JSON-LD/meta/Schema/页面文案时**直接给可复制代码**，不写「建议补 XX schema」。
- **严禁编造**：企业名/地址/产品参数/社交账号 URL/成立年份缺失时标 `> ⚠️ 信息不足，需用户补充：xxx`。
- URL 字段必须给完整可点击链接，不写页面名称。

## 聚合度自检（写完必过）

1. 任务总数是否在 8–14 区间？
2. 有无 ≥2 个任务「涉及页面」完全相同可合并？
3. 有无 ≥2 个任务「前置依赖」互相重叠（本质同一件事）？
4. **双向 P0 对齐**：每个 P0 任务是否都对应 `p0_blockers` 里的一条阻塞项？**且 `p0_blockers` 里每条阻塞项是否都有一条对应任务（一条不漏）**？
5. **Critical 全覆盖**：scores.json 里每条 Critical finding 是否都在清单中有对应任务？
6. 有无任务其实是另一任务的子步骤（写 sameAs 数组 vs 注册 Wikidata 词条）？