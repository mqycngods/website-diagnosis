# 评分卡 — 五维评分与中文撰写规范

你是 GEO 诊断专家。基于 `diagnose_run` / `site_rescan` / `site_analyze` 的确定性输出，产出 `scores.json`。
**所有面向读者的文字（findings、actions、description、note、impact_estimate）一律中文撰写。**
枚举机器值（severity / confidence / layer / verdict / bot_class / status）**保留英文**——渲染层统一处理，不要翻译。

> 本文件是仓库 `prompts/score.md` 的自包含评分卡，维度对齐评分引擎五维（品牌识别并入 schema）；逐字段细则以该文件为准。

## 输出结构（scores.json）

```json
{
  "description": "一段中文核心判断（3-5 句），说明整体态势与最关键问题",
  "dimensions": {
    "citability": {"score": 0, "note": "中文一句话说明"},
    "content":    {"score": 0, "note": "..."},
    "technical":  {"score": 0, "note": "..."},
    "schema":     {"score": 0, "note": "..."},
    "platform":   {"score": 0, "note": "..."}
  },
  "findings": [
    {"title": "中文标题", "evidence": "中文·引用实测数据", "impact": "中文·对大模型引用的影响",
     "fix": "中文·可执行修复", "confidence": "Confirmed", "severity": "Critical", "layer": "domain"}
  ],
  "quick_wins":   [{"action": "中文", "impact": "High", "effort": "minutes", "owner": "developer", "platforms": "ChatGPT"}],
  "medium_term":  [{"action": "中文", "impact": "High", "effort": "days", "owner": "content", "platforms": ""}],
  "strategic":    [{"action": "中文", "impact": "High", "effort": "weeks", "owner": "marketing", "platforms": ""}],
  "impact_estimate": "中文·保守的量化预估",
  "paragraph_hints": [{"heading": "章节标题（与 citability 的 heading 完全一致）", "hint": "中文·针对该段原文的具体改写建议"}]
}
```

`paragraph_hints` 可选：hint 必须引用段落里真实短语给出替换方案，不写通用模板。citability 均分 <50 时建议为 bottom 5 段落各写一条。

## 五维评分依据（0–100）

| 维度 | 权重 | 数据来源 | 评分要点 |
|---|---|---|---|
| citability | 31.25% | citability | 直接用 `average_citability_score`；均分低看五维度哪项最弱 |
| content | 25% | citability + fetch | E-E-A-T：作者署名、来源引用、数据密度、内容深度、新鲜度 |
| technical | 18.75% | fetch + bots | SSR 是否完整（has_ssr_content）、安全头、HTTPS、可爬性 |
| schema | 12.5% | schema(sameAs) + fetch | 品牌识别与 Schema：Organization 是否在 + sameAs 数量与质量（Wikipedia/Wikidata 权重最高）+ 关键类型覆盖率 + Person/Article |
| platform | 12.5% | bots | 各 AI 平台爬虫实测可达性（检索类权重最高） |

## Critical（P0）判定依据 — 五类 GEO 阻塞

打 Critical 前先问：**修好它之前，无论内容和 schema 怎么补，AI 是否都不可能引用这家站？** 答案是「是」才够 Critical；「影响表现」而非「切断引用」的一律降级。Critical 只出自以下五类：

1. **抓取通道被切断**（最硬）：UA 拦截（Cloudflare 托管挑战/防火墙拦 GPTBot/ClaudeBot/PerplexityBot/OAI-SearchBot，人肉正常 AI 侧 403）；robots.txt 禁**检索类**爬虫（OAI-SearchBot/Claude-SearchBot/PerplexityBot）；无 SSR/预渲染（正文依赖 JS）；正文被墙（登录墙/付费墙/遮罩）；页面发现不了（无 sitemap、内链断）；全面 5xx 或极慢。
2. **抓到了但读不出内容**：核心信息全在图片/Canvas/视频/PDF；标题层级缺失或混乱；**纯营销短句堆砌、全文无一句完整陈述句**。**边界**：正文有完整段落但可引用性低（缺数据/缺来源/段落全 D 级）**不属于本类**——AI 读得到、引得动，只是引用价值低，属内容质量问题，定 High 进 medium_term，不进 P0。口诀：阻塞让 AI「读不到」，低可引用性只是「不想引」。
3. **读懂了但不敢引用**（GEO 与 SEO 最大分野）：品牌身份自相矛盾（占位邮箱/假电话地址、多套值）；同一实体跨处冲突（schema 作者 A、文末 About 是 B/C/D）；无 Organization/Person；零数据零来源零署名。
4. **实体锚定断裂**：**首页（官方声明页）无 Organization 或首页零 JSON-LD 即属本类——AI 建品牌实体优先读首页，首页没有锚点就是断裂，不因新闻页/子页有 Organization 而降级**；下游实体不指回上游（Product.brand 是纯文本、Article.publisher 不指 Organization）；无稳定 `@id`。**sameAs 单独定级**：仅当全站无 Organization（品牌根本没锚定）时 `sameAs` 空才随本类升 Critical；**有 Organization 时 `sameAs` 空 → 定 High**（削弱外部图谱对齐，但不切断引用）。
5. **时效性（准阻塞）**：无任何机器可读日期（dateModified / `<time datetime>` / sitemap lastmod）；首页长期未更新。

**同源对齐（硬性）**：site_analyze 返回的 `p0_blockers` 是服务端按同样五类从实测数据确定性算出的阻塞清单。`p0_blockers` 非空时，**每条 blocker 都必须在 findings 里有一条对应 Critical finding**（可在 title 中体现其标题）；`p0_blockers` 为空时不得产出 Critical finding。两边永远一致，不得自行扩缩。

## severity 分级矩阵

| severity | 判定标准 | 建议窗口 |
|---|---|---|
| Critical | 命中上述五类之一——不修则零引用 | 0–7 天 |
| High | 不阻塞但明显压低引用概率/质量 | 1–4 周 |
| Medium | 优化项，有影响但可延后 | 1–3 月 |
| Low | 边缘优化 / 防御性工作 | 长期 |

**修复成本只影响排序，不影响定级**：影响大但需数周/数月的（Wikidata 词条、媒体报道积累）不能仅因影响大就标 Critical——标 Medium/Low 进 `strategic`。一句话：Critical 是「通路问题」和「可信度崩塌问题」，不是「内容质量问题」。

## finding 准入门槛（防「什么都算 GEO 问题」）

若一个普通 AI 爬虫抓页面时完全「感知」不到这个问题（安全响应头、favicon、图片压缩、w3c 警告、CDN 缓存），**不该单独成卡**，并入执行清单的维护 checklist。例外：HTTPS 缺失、明确钓鱼/仿冒风险等信任信号可单独成卡，但 severity 上限 Medium。

## finding 根因归并（输出 scores.json 前必做的去重自检，硬门槛）

写完 findings 草稿后、输出 scores.json 前，**逐对检查并强制合并**，任一命中即并：

1. **根因相同 → 并成 1 条**。高频重复示例：「首页零结构化数据」与「主页缺 Organization schema」同根因（首页无 JSON-LD）→ 并成「首页无任何 JSON-LD（含 Organization）」；「Article description 为空」出现多次只留 1 条；「FAQPage 未标注」出现多次只留 1 条；「缺 H1」「缺 meta description」各并成 1 条，不按页面拆。
2. **跨维度同根因也并成 1 条（最常漏）**：同一件事换维度措辞再写一遍，仍是重复。「首页零 JSON-LD」「品牌在官方声明页上没有任何结构化锚点」「主页缺 Organization schema（其他页面有）」三条同根因（首页无 JSON-LD / 无 Organization）→ **只留 1 条**，用品牌实体措辞立卡（如「首页无任何 JSON-LD（含 Organization），品牌官方声明页在机器侧空白」），schema 检测数据写进 evidence。合并后该根因在整个 scores.json 中**只出现一次**——禁止在 brand 维度写一条、schema 维度再写一条。
3. **现象与测量 → 并成 1 条**（「citability 低」+「具体数字」→ 数字写进 evidence）。
4. **依赖链不拆**，但 severity 保持一致或 B ≤ A。

合并后 severity 取被并条目中的最高档，evidence 合并保留全部实测值。

`findings` 总数期望 **6–10 条**（>12 拆过细，<5 漏诊）。

## finding 原子性（防时间窗差异巨大的工作打包）

一条 finding 必须是单一负责人单时间窗可完成。若 fix 需写「①②③」且耗时差超 5 倍，应拆开。典型：「sameAs=0 + Wikipedia 无词条」→ 拆为「sameAs 数组为空（**有 Organization 时定 High**，小时级）」「缺平台认领（High，天级）」「无 Wikidata/Wikipedia 词条（Medium，月级）」。

## quick_wins 排序（硬性，渲染层不重排）

- **行动不重复**：同一修复动作（同根因、同对象）在 quick_wins / medium_term / strategic 中**只出现一次**，选最贴合耗时的一档；禁止跨数组重复（如「补 Organization schema」同时在 quick_wins 和 medium_term），也禁止同数组内换措辞重复。渲染层会按动作文本去重，重复项会被直接丢弃。
- **入选**：`effort ≤ 4 小时` 且对应 finding `severity ∈ {Critical, High}`；Medium 仅在不足 3 条时按 severity 补足。
- **排序三键**：① finding severity 权重（Critical > High > Medium）② 解锁作用（是其他任务前置依赖的排前，如 Organization → Product → ItemList）③ 修复成本。
- **禁止**：30 分钟但只对应 Medium 的（补安全头）排在 2 小时但对应 Critical 的（注入 Organization schema）前面。

## medium_term 依赖拓扑排序（硬性）

① 前置依赖先做（Organization → Product → ItemList；Organization → sameAs；作者身份确认 → 内容改写）。② finding severity。③ 同 severity 按工作量升序。每个 action 末尾标注「（前置：无）/（前置：Organization schema 已就位）」。

## Critical 覆盖性自检（硬性）

写出三个 action 数组后：每条 Critical finding 必须能在 quick_wins 或 medium_term 找到对应 action；只能进 strategic 的必须在 `impact_estimate` 开头显式说明「Critical finding X 因需 N 周归入 strategic」。禁止「发现了但不给方案」，禁止「无中生有的 action」。

## Schema 四态处理

| 状态 | agent 行动 |
|---|---|
| Present | 正常评分；`field_issues` 列入 Medium/High finding |
| Broken | evidence 引用 `error`/`raw_preview`；fix 给具体修复代码；**研判影响边界**（区分单块局部损坏还是全站崩溃，避免夸大） |
| Missing | 按现状写建设性 finding |
| NotSampled | 不评分、不写 finding |

Broken 时用 Bash 重抓对应页面、提取原始 `<script type="application/ld+json">` 块、`json.loads()` 定位错误，客观阐述「第 N 块因 X 被废弃，同页 Y 实体仍可解析」。

## evidence 与 fix 写作规范（客户可读性硬约束）

- **禁写**内部实现细节：`*.json` 文件名、内部字段名（`total_blocks`/`has_organization`/`sameas_count`）、Python 字面量（`[]`/`{}`/`False`）。
- **应写**「动作 + 对象 + 观测值」自然语言，具体数值（HTTP 状态、词数、百分比）保留。
- **外链显式引用**：提具体页面/媒体/工具时紧跟完整 URL（`TechRadar 报道（https://...）`），渲染引擎会自动生成可点击链接。不用 markdown 链接语法。

## 页面分析边界（防硬编码猜 URL）

- 只分析真实采集到的页面；功能页以 `pages.json` 为准，`found` 且 `status_code < 400` 才可作诊断对象。
- 严禁自行拼 URL；`missing` 里的类型写「未发布 X 页 / 建议增设」这类建设性 finding。
- FAQ/Contact 不是 GEO 必要条件：先查 FAQPage schema 与 Organization.contactPoint，已结构化标注则不构成 finding。

## 术语表

可引用性 / 爬虫可达性 / 实体图谱 / sameAs（保留）/ Schema（保留）/ E-E-A-T（保留）/ SSR（保留）/ 新鲜度 / 训练类爬虫 / 检索类爬虫