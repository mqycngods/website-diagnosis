---
name: site-diagnosis-zh
description: 中文官网 GEO 优化报告生成器。输入一个 URL，产出一份带图表的单文件中文 HTML 客户交付报告。诊断 AI 可引用性、爬虫可达性、Schema、品牌实体、技术基础，由 agent 完成评分与中文撰写，脚本完成确定性采集、报告数据构建与渲染。适用于 Claude Code、Codex 及任意能执行 Python 与读写文件的 agent 环境。
---

# 中文官网 GEO 优化报告（site-diagnosis-zh）

输入一个网址，输出一份自包含的中文 GEO 官网优化报告（单文件 HTML，内联风格与图表）。

## 设计要点（读前先懂）

- **脚本做确定性的事**：抓页面、解析 robots、重放爬虫 UA、给段落打分、构建报告数据、渲染 HTML。同一 URL 任何机器结果一致。
- **agent 做判断与写作**：读采集到的 JSON，按评分卡打分、用**中文**写发现与建议。**汉化不靠翻译——评分时直接用中文写**，从源头保证准确。
- **执行计划由 agent 生成，不进渲染层**：`GEO_EXECUTION_PLAN.md` 是诊断后的独立交付物（Phase 2.5），由 agent 基于 `work/scores.json` 直接撰写 Markdown；**不要为它写生成器脚本**——任务合并/拆分、依赖排序、最小执行路径都是判断工作，硬编码规则套不上不同站点。
- **Report Builder 做数据转换**：`scripts/report_builder.py` 将原始诊断数据转换为客户报告数据对象（Executive Summary、Top Priorities、Page Recommendations、Priority Actions、Roadmap）。
- **零 API key**：不调用任何外部 LLM 接口；跑这个 skill 的 agent 就是 LLM。

## 依赖

```bash
pip install -r requirements.txt   # requests, beautifulsoup4, lxml, jinja2
```

## 执行流程（三步）

### Phase 0 — 建立本次诊断的输出目录（每次诊断必做）

每次诊断开始前，先为本次诊断建一个独立文件夹，**所有产出（JSON、HTML、执行计划 MD）都放进这个文件夹，不再写到项目根目录**。

```bash
# 生成文件夹名：<域名>-<yyyymmdd-HHMMSS>
DOMAIN=$(python3 -c "from urllib.parse import urlparse; import sys; print(urlparse(sys.argv[1]).netloc.replace('www.','').replace(':','_'))" "<url>")
TS=$(date +%Y%m%d-%H%M%S)
RUN_DIR="results/${DOMAIN}-${TS}"
mkdir -p "$RUN_DIR/work"
echo "$RUN_DIR"
```

后续所有 `work/` 路径都替换为 `$RUN_DIR/work/`；HTML 报告与 `GEO_EXECUTION_PLAN.md` 也直接写入 `$RUN_DIR/`。

最终目录形态：

```
results/
└── <domain>-<yyyymmdd-HHMMSS>/
    ├── work/                     # 采集与评分的所有 JSON
    │   ├── fetch.json
    │   ├── robots.json
    │   ├── bots.json
    │   ├── citability.json
    │   ├── schema.json
    │   ├── brand.json
    │   ├── technical.json
    │   ├── articles.json
    │   ├── pages.json
    │   └── scores.json
    ├── <domain>-geo-report.html  # Phase 3 渲染产物
    └── GEO_EXECUTION_PLAN.md     # Phase 2.5 由 agent 直接 Write
```

历史诊断保留在各自文件夹中互不覆盖；项目根目录不再堆 `*-geo-report.html` 与 `work.bak.*`。

### Phase 1 — 确定性采集（跑脚本）

```bash
python report.py collect <url> --out "$RUN_DIR/work/"
```

产出 `$RUN_DIR/work/` 下的 JSON：

| 文件 | 内容 |
|---|---|
| `fetch.json` | 页面 title/meta/标题层级/正文/structured_data/响应头 |
| `robots.json` | robots.txt 解析 + 各 AI 爬虫声明策略 |
| `bots.json` | AI 爬虫 UA 重放实测（谁被拦/被放行/被收费） |
| `citability.json` | 正文段落可引用性逐段评分 + 页面均分 |
| `schema.json` | JSON-LD 清单 + sameAs 链接 |
| `brand.json` | 品牌站外实体（Wikipedia/Wikidata API 验证） |
| `technical.json` | 安全头矩阵 + HTTPS + llms.txt |
| `articles.json` | 抽样文章页（新鲜度与 Article/Person schema 是文章级信号） |
| `pages.json` | 功能页发现（Support/FAQ/Contact/About/Warranty）：`found` 为真实抓到、`missing` 为未发布——agent 只诊断 `found`，对 `missing` 写建设性建议，不虚构 URL。**注意两个例外**：① FAQ —— 若 `schema.json` 显示 FAQPage 已 Present（内嵌在博客/产品页），则「缺独立 FAQ 页」不构成 GEO finding；② Contact —— 若 Organization 已配置 `contactPoint` 或首页/页脚/About 页正文有可见联系方式，则「缺独立 Contact 页」不构成 GEO finding |

某一步失败不阻断：对应 JSON 写 `{"error": "..."}`，报告该章渲染"未测量"。

### Phase 1.5 — Agent 辅助页面采样（**仅当 articles.json 带 `pending_agent_pick: true` 时执行**）

正则采样依赖 URL 路径特征（`/news/`、`/products/` 等）。对 `index.php?classid=N` 这类动态 URL 站点会失效，此时 `articles.json` 包含 `pending_agent_pick: true` 与完整 `internal_links`。

**Agent 的操作**：
1. 读 `$RUN_DIR/work/articles.json` 的 `internal_links`，从链接文本与 URL 模式识别文章/产品详情页（通常文本为具体标题/型号，而非「更多产品」「了解更多」这类通用按钮；URL 通常带 `id=N` 参数）
2. 在 `$RUN_DIR/work/agent_picks.json` 写入：
   ```json
   {"articles": ["url1", "url2", "url3"], "products": ["url4", "url5"]}
   ```
3. 执行 `python report.py fetch-sampled "$RUN_DIR/work/"`，脚本抓取这些 URL、回写 `articles.json`、用最新数据重建 `schema.json`
4. 继续 Phase 2

数量建议：文章 3-5 篇、产品 2-3 个。避免重复采同一栏目。若 internal_links 里确实没有可识别的详情页（纯单页站），跳过本阶段，schema 的 Article/Product 行维持 NotSampled。

### Phase 2 — Agent 评分与中文撰写（你来做，读 prompts/score.md）

读取 `$RUN_DIR/work/*.json`，按 `prompts/score.md` 的评分卡，产出 `$RUN_DIR/work/scores.json`：

- 六个维度各给 0–100 分：可引用性 / 品牌实体 / 内容质量 / 技术基础 / Schema / 平台就绪
- 每条 finding 用**中文**写：title / evidence / impact / fix / confidence
- 给 3 条本周速赢、2 条本月改进、1 条本季战略（中文，含影响/工作量/负责人）
- 自由文本一律中文；枚举机器值（severity/confidence/bot_class/verdict 等）**保留英文**——渲染层统一处理

**写完必须验证 JSON 合法（硬性门槛，不过不进 Phase 3）：**

```bash
python3 -m json.tool "$RUN_DIR/work/scores.json" > /dev/null && echo OK
```

中文文本里引用词组时优先用中文引号「」或『』；若必须用英文双引号，注意它们在 JSON 字符串内必须转义为 `\"`。验证失败就修到通过为止——渲染层对非法 JSON 只能整份忽略，agent 的评分、findings、行动计划会全部静默丢失。

### Phase 2.5 — 生成 GEO 执行计划（agent 撰写，可选但推荐）

诊断报告回答「问题是什么」，执行计划回答「今天该做哪几件事」。两者面向不同读者（前者给客户/决策者，后者给运营/开发/内容执行人），**不要合并**。

**产出位置**：`GEO_EXECUTION_PLAN.md`，放在 `$RUN_DIR/` 下（与最终 HTML 报告并排），由 agent 直接用 Write 工具写出，**不经过 render**。

**数据来源（只能从这里取，禁止重新诊断）**：

- `$RUN_DIR/work/scores.json` 的 `findings` / `quick_wins` / `medium_term` / `strategic`
- `$RUN_DIR/work/*.json` 里的实测值（HTTP 状态、词数、URL、爬虫名单、sameAs 缺失项等），用于填充任务的 `evidence` 与「可直接使用」代码块

**任务提取规则**：

- **合并**：同一根因引发的多个 finding 合并为一个任务（例：GPTBot 被拦 + ClaudeBot 被拦 + robots.txt 规则错误 → 合并为「修复 robots.txt 的 AI 爬虫访问规则」一个任务）
- **拆分**：一条建议若实际包含多个可独立完成/验证的工作，拆成多个任务（例：「优化产品页」→ 拆「补 Product schema」/「清洗 description HTML 实体」/「补 brand.@type」/「补 review 数组」）
- 判断标准：拆出来的每个任务都能独立勾选完成、独立验证

**任务粒度约束（防碎片化，重要）**：

- **数量预期**：单站执行清单总任务数应控制在 **8–14 项**之间。少于 8 项说明聚合过度（可能漏了独立可验证的工作），多于 14 项几乎一定是拆得过碎。若草拟出 15+ 项，先停下来自检是否能合并。
- **页面级聚合**：同一页面的同类改动（例如主页的多个内容区块重写、同一产品的多个 schema 字段修复）默认**合并为一个任务**，在执行清单内用子项列出，而不是每个区块/每个字段各起一张任务卡。判断依据：这些改动共享同一份「涉及页面」清单、同一个负责人、同一次发布上线。
- **实体级聚合**：同一实体的多个字段（如 Organization 的 name / logo / sameAs / foundingDate / address / contactPoint）必须合并为一个任务。站点级实体（Organization + WebSite）通常也合并，因为它们注入位置相同、@id 互相引用、同时上线。
- **阶段级聚合**：长期建设工作（如「建立品牌外部档案」）按工作流拆，不按平台拆。LinkedIn/YouTube/Crunchbase/Trustpilot/Wikidata 这些平台不要每个起一张卡，但也不要笼统打成一个 P3——按工作流内的「见效快慢」拆成两段：
  - **P1 段（本月可完成）**：注册/认领已有平台公司页（LinkedIn / YouTube / Crunchbase / Trustpilot / Amazon Brand Store），把已核验 URL 写进 Organization.sameAs。这部分工作量小、直接影响 sameAs 是否生效，应归 P1。
  - **P3 段（长期）**：Wikidata / Wikipedia 词条。需要积累 2 篇以上独立主流媒体报道，可能耗时数周到数月，归 P3。
- **不拆的红线**：当拆分会导致多个任务的「涉及页面」「前置依赖」「负责人」完全相同时，说明不该拆。每个任务卡必须有自己独立的执行上下文。
- **P0 任务聚焦**：P0 只放真正阻塞性 + 修复成本低的工作，理想 3–5 项。如果 P0 超过 6 项，说明有的任务其实是 P1（重要但不阻塞），降级。
- **P2/P3 取舍**：与 GEO 弱相关的顺手优化（如补安全头、加 referrer-policy）合并到维护任务里作为 checklist，**不单独占任务卡**——除非该问题被评分卡定为 Critical/High。

**任务粒度自审清单**（写完后必过一遍）：

1. 任务总数是否在 8–14 区间？
2. 是否有 ≥2 个任务的「涉及页面」完全相同？若有，能否合并为一个任务，用子项表达？
3. 是否有 ≥2 个任务的「前置依赖」互相重叠？若有，说明它们本质是同一件事的不同侧面。
4. 每个 P0 任务是否都是「不做就阻塞其他工作」或「修复成本低但影响面大」？否则降到 P1。
5. 是否有任务做的事其实是另一任务的子步骤（如「写 sameAs 数组」和「注册 Wikidata 词条」本质是同一条 brand 工作流）？

**任务字段（每个任务必填）**：

```text
task_id          TASK-001 起递增
title            动作 + 对象（不写"优化 XX"这种虚词）
priority         P0 / P1 / P2 / P3
category         技术基础 / Schema 与实体 / 内容与可引用性 / 品牌外部 / 平台与爬虫
estimated_time   具体到分钟/小时（"10 分钟" / "1–2 小时"），禁用"较短/适中/较长"
difficulty       简单 / 中等 / 较复杂
dependencies     前置 task_id 列表，无则写「无」
why              这个任务解决什么问题（引用诊断 finding）
steps            可执行步骤（1/2/3…）
deliverable      产出物（哪个文件、哪个字段、哪个页面）
verification     完成标准 + 验证方式
status           未开始 / 进行中 / 已完成
```

**优先级判定**：

| 级别 | 触发条件 |
|---|---|
| P0 | 严重技术问题 / 阻碍 AI 爬虫访问 / 其他任务的前置 / 修复成本低但影响面大 |
| P1 | 对核心 GEO 能力有明显帮助：品牌实体（含 Organization schema、sameAs 配置、**已有平台官方账号注册与回链**）、Schema 关键类型、核心产品/服务页、内容结构 |
| P2 | 内容增强、FAQ、页面细节、引用信号、辅助内容 |
| P3 | 低影响优化、长期内容建设（如 Wikidata / Wikipedia 词条、持续 Digital PR）、非核心页面、可选增强 |

**排序逻辑（不要按 P0→P1 机械排）**：综合 优先级 × 耗时 × 依赖 × 难度，输出真实可执行的顺序。例：补 sameAs 依赖 Organization 存在；作者冲突修复要先于内容改写；P0 里 5 分钟的排前面、半天的排后面。

**必须包含的视图**（已融入结构骨架）：

1. **诊断基线**：六维评分表 + 主要证据 + 阶段目标 + 「无需立即改动」豁免说明
2. **资料准备清单**：执行前必须确认的真实数据（公司事实 / 账号 / 作者 / 政策 / 量化数据 / SKU / 评价 / 媒体报道 / 主题副本）
3. **优先级与建议排期**：P0/P1/P2/P3 + 维护 表格，含任务编号区间与时间窗口
4. **逐项执行与验收**：每项含元信息表格 + 诊断依据 + 涉及页面 + 执行清单 + 代码块 + 验收清单
5. **后续验证**：融入每个任务的「验收清单」，并保留「重新跑 collect + 评分」作为收尾

**「可直接使用」代码块**：涉及 robots.txt / llms.txt / JSON-LD / meta / Schema / 页面文案 / 爬虫规则时，**直接给可复制的代码**，不要只写「建议补 XX schema」。**严禁编造**：企业名、地址、产品参数、社交账号 URL、成立年份等事实缺失时，明确标 `> ⚠️ 信息不足，需用户补充：xxx`。

**禁止事项**：

- 不重复诊断结论（不重新打分、不复制 findings 大段正文）
- 不做收益承诺（禁写「完成后 GEO Score 提升 N 分」，只写「完成后建议重新诊断」）；阶段目标列的分数仅作方向指引，非承诺
- 不用 Emoji 作为主要信息结构；不用 HTML/Mermaid；不用复杂图表（真实性红线 callout 的 ❗ 除外）
- 列表缩进统一 2 空格；代码块用标准 fenced block
- 执行清单与验收清单统一使用 `- [ ]` checkbox 语法，任务状态使用四态「未开始 / 进行中 / 待验收 / 已完成」
- 「涉及页面」「资料存放链接」「证据链接」等 URL 字段必须给出完整可点击链接，不要只写页面名称

**Markdown 结构骨架**（飞书交付版，按此顺序）：

```markdown
# {品牌} GEO 优化执行清单｜基于 {YYYY-MM-DD} 诊断报告

> 诊断对象：{url}
> 诊断依据：{report.html 文件名（位于本次诊断的 results/<domain>-<ts>/ 内）}｜报告日期：{date}
> 采样范围：{首页、N 篇文章、M 个产品页、功能页清单}；AI 爬虫实测 N 个 User-Agent
> 文档版本：报告对齐执行版 vX.Y
> 使用方式：执行人员完成一项即勾选；每个任务的验收清单全部通过后，再将任务状态改为「已完成」。

> ❗ **真实性红线**：成立时间、公司地址、用户数、覆盖国家、响应速度、产品参数、保修政策、媒体评分、作者身份和社交账号必须有可核验来源。无法确认时应删除相关字段，不得把占位值、推测数据或虚构评价发布到线上。

## 一、诊断基线与优化重点
{一段综合判断 + 六维基线表格：维度 | 基线 | 主要证据 | 阶段目标}
{一段「无需立即改动的部分」：已正常的项明确豁免，防止执行人员过度优化}

## 二、开始前资料准备清单
{checkbox 列表，覆盖：公司事实 / 官方账号 / 作者身份 / 联系方式 / 政策 / 量化数据 / 主推 SKU / 评价 / 媒体报道核对 / Shopify 主题副本与证据目录}
**资料存放链接**：________　　**资料确认日期**：________

## 三、优先级与建议排期
{表格：级别 | 任务编号区间 | 报告建议窗口 | 完成情况}
{一段「建议首批上线」说明}

## 四、逐项执行与验收清单
{每个任务按以下模板，任务间用 --- 分隔}

### N. {任务标题}
| 字段 | 值 |
|---|---|
| **优先级** | P0/P1/P2/P3｜严重/高/中/低 |
| **预计工作量** | 具体到分钟/小时/工作日 |
| **建议负责人** | 角色组合（如「内容负责人 + Shopify 开发」） |
| **前置依赖** | 前置任务编号或资料确认 |
| **任务状态** | ☐ 未开始　☐ 进行中　☐ 待验收　☐ 已完成 |
| **计划完成日期** | ________ |
| **实际完成日期** | ________ |
| **证据链接** | ________ |

**诊断依据。** {引用 finding 的实测数据与影响，可附「已修正」说明如果原 finding 是误报}

**涉及页面。**
{markdown 链接列表}

**执行清单**
{checkbox 列表，每条对应一个可独立验证的动作}

**{代码块标题}｜发布前替换或删除所有说明值**
```{json/liquid/bash/text}
{可直接复制的代码}
```

**注意事项。** {可选，关键陷阱提醒}

**验收清单**
{checkbox 列表，每条对应一个可观测的完成标准}
{最后一条固定为：已填写实际完成日期和证据链接，并由验收人员确认}
```

**判断标准**：执行人员打开文档后能立刻知道：(1) 本周先做哪几件 P0；(2) 每件事的「执行清单」逐条勾完即可进入验收；(3) 验收清单全部通过才能把任务状态改为「已完成」；(4) 不需要再翻诊断报告就能拿到所有必要的代码、URL 与字段值。如果做不到，继续优化任务拆分与排序。

**聚合度自检**：写完后对照上面的「任务粒度自审清单」。如果你发现执行清单出现「同一页面拆 3+ 张卡」「同一实体拆 2+ 张卡」「同一路线（如外部品牌建设）拆 2+ 张卡」「顺手优化（如安全头）单独成卡」中的任一情况，回到合并步骤重做，不要为了凑数量而拆。

### Phase 3 — 渲染

```bash
python report.py render "$RUN_DIR/work/" --domain <domain> --url <url> --output "$RUN_DIR/<domain>-geo-report.html"
```

生成单文件中文 HTML：内联 CSS（暖纸风格）+ ECharts 图表（SVG，可打印）+ 内嵌原始数据 JSON。文件与 `GEO_EXECUTION_PLAN.md`、`work/` 一起归档在 `$RUN_DIR/` 内。

## 评分权重（六维合成 GEO 总分）

可引用性 25% · 品牌实体 20% · 内容质量 20% · 技术基础 15% · Schema 10% · 平台就绪 10%

## 报告结构（客户交付版）

报告按「总览 → 诊断 → 行动 → 证据」的决策路径组织，分为 3 个 Group + 附录。Group 是信息层级——同一 Group 内的子章节共享同一个阅读目标。

```
Cover
│
├── GROUP 01 — 执行总览（Executive Overview）
│   │   回答「这个网站目前 GEO 表现怎么样？」 · LEVEL 1 · 决策层
│   ├── 执行摘要（核心判断 / 一句话结论）
│   └── GEO 就绪度评分（综合仪表盘 + 五维雷达）
│
├── GROUP 02 — 诊断分析（GEO Diagnosis）
│   │   回答「为什么是这个结果？」 · LEVEL 3 · 证据层
│   ├── AI 可发现性（爬虫实测 / robots.txt / crawlability）
│   ├── 技术基础与新鲜度（HTTPS / 安全头 / llms.txt / 时效性）
│   ├── 实体与结构化数据（Organization / WebSite / Product / sameAs）
│   └── 内容与可引用性（citability / answerability / 内容质量）
│
├── GROUP 03 — 行动方案（Action Plan）
│   │   回答「怎么做？」 · LEVEL 2 · 决策与执行一体
│   │   每个 Issue 只写一次正文，按优先级 / 页面 / 时间三种维度组织。
│   ├── 主视图：按优先级分桶（P0 立即 / P1 高优 / P2 优化 / P3 长期）
│   │   每张行动卡片含 #01 锚点 + 为什么重要 / 证据 / 建议动作 / 代码示例
│   ├── 折叠视图：按页面查看（首页 / 产品页 / 关于 … 只列编号）
│   └── 折叠视图：按时间查看（30/60/90 天 … 只列编号）
│
└── APPENDIX — 附录
        技术证据 · 详细数据 · 方法论 · LEVEL 3 · 证据
    └── 全部 Findings（含证据、影响、置信度、所在层）
```

**信息降级原则**：LEVEL 1（执行信息）> LEVEL 2（行动方案）> LEVEL 3（技术证据）。
**去重原则**：每个 finding 在 `report_builder.py` 派生时只产出一条 `priority_action`（带 `finding_index`），page_recommendations 与 roadmap 通过 `finding_index` 引用，不再复制正文——同一份内容全文只写一次。

## 目录

```
diagnose/        确定性采集脚本
render/          ReportData 组装 + 图表预计算 + Jinja2 渲染
scripts/         report_builder.py（客户报告数据构建）
templates/       中文报告模板（Jinja2）
prompts/         给 agent 的评分卡与汉化契约
report.py        唯一 CLI 入口（collect / render 两个子命令）
results/         每次诊断一个独立子文件夹（<domain>-<yyyymmdd-HHMMSS>/），
                 内含 work/（所有 JSON）、HTML 报告、GEO_EXECUTION_PLAN.md
```

## 图表选择逻辑

不是所有数据都必须画图。`render/charts.py` 内置 `should_render_chart` 逻辑：

- 数据不足时自动隐藏
- 数据只有一个值时不显示（无比较关系）
- 图表无法产生结论时不显示
- 不为了视觉效果强行生成图表

## 优先级定义

| 优先级 | 含义 | 示例 |
|---|---|---|
| P0 紧急 | 阻碍 GEO 基础能力 | AI 爬虫无法访问、核心页面无法抓取 |
| P1 高优 | 对 GEO 有明显影响 | 品牌定义不清、核心产品信息不足 |
| P2 优化 | 优化项 | Schema Enhancement、FAQ、内容结构优化 |
| P3 长期 | 长期建设 | Digital PR、第三方权威、Knowledge Graph |
