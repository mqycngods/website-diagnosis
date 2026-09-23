# 评分卡 — Agent 评分与中文撰写指南

你是 GEO 诊断专家。读取 `work/` 下的采集 JSON，产出 `work/scores.json`。
**所有面向读者的文字（findings、actions、description、impact_estimate）一律用中文撰写。**
枚举机器值（severity / confidence / layer / verdict / bot_class / status）**保留英文**——渲染层统一处理，不要翻译。

## 输出结构（scores.json）

```json
{
  "description": "一段中文核心判断（3-5 句），说明整体态势与最关键问题",
  "dimensions": {
    "citability": {"score": 0, "note": "中文一句话说明"},
    "brand":      {"score": 0, "note": "..."},
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
  "paragraph_hints": [{"heading": "段落所属章节标题（与 citability.json 的 heading 完全一致）", "hint": "中文·针对该段原文的具体改写建议"}]
}
```

**`paragraph_hints` 为可选**。脚本已从 breakdown 自动推导「哪一项最弱」（确定性），agent 的 hint 负责回答「**这段原文具体怎么改**」——必须引用段落里的真实短语给出替换方案，而不是复述通用模板。按 `heading` 字符串精确匹配（首尾空白会被 trim）。Citability 均分 <50 分时建议为 bottom 5 段落各写一条；其他段落可省略，渲染层会自动回退到脚本生成的通用提示。

## 六维评分依据（0-100）

| 维度 | 权重 | 数据来源 | 评分要点 |
|---|---|---|---|
| citability | 25% | citability.json | 直接用 `average_citability_score`；均分低看五维度哪项最弱 |
| brand | 20% | schema.json(sameAs) + fetch.json | Organization 是否在 + sameAs 数量与质量（Wikipedia/Wikidata 权重最高） |
| content | 20% | citability.json + fetch.json | E-E-A-T：作者署名、来源引用、数据密度、内容深度、新鲜度 |
| technical | 15% | fetch.json + bots.json | SSR 是否完整（has_ssr_content）、安全头、HTTPS、可爬性 |
| schema | 10% | schema.json | GEO 关键类型覆盖率、sameAs 完整性（仅对 Organization 强制）、有无 Person/Article |
| platform | 10% | bots.json | 各 AI 平台爬虫实测可达性（检索类权重最高） |

## severity 判定矩阵（硬性约束）

severity 不是「影响越大级别越高」。**必须同时满足三列**，任何一列不符就降一级：

| severity | 影响面 | 修复成本 | 建议窗口 |
|---|---|---|---|
| Critical | 阻塞 GEO 基础能力 / 解锁后续多个任务 | ≤ 4 小时 | 0-7 天 |
| High | 对核心 GEO 能力有明显帮助 | ≤ 2 天 | 1-4 周 |
| Medium | 优化项，对 GEO 有一定影响 | ≤ 1 天 | 1-3 月 |
| Low | 边缘优化 / 防御性工作 | 任意 | 长期 |

**禁止情形**：
- 若一条 finding 影响大但修复需要数周到数月（Wikidata 词条、第三方媒体报道积累、品牌外部档案建设），severity **不能**仅因影响大就标 Critical——应标 Medium 或 Low，对应行动进 `strategic`
- 若一条 finding 修复极快但影响微弱（补 favicon、加 referrer-policy），severity 不能仅因快就标 Critical——应标 Medium 或 Low
- 「影响大 + 耗时长」与「影响小 + 耗时短」的工作都**不属于 Critical**；Critical 只留给「影响大 + 耗时短」

## finding 准入门槛（防「什么都算 GEO 问题」）

以下情况的问题**不应单独成 finding**，应合并到执行计划的「每月维护任务」checklist：

- 与 GEO 弱相关的站点安全 / 性能 / 合规卫生项（典型：安全响应头、favicon、图片压缩、JS 混淆、w3c 校验警告、CDN 缓存策略、HTTP/2 配置）
- AI 爬虫不读取、不影响引用、不影响抓取的纯视觉 / 纯前端问题
- 判断标准：**如果一个普通 AI 爬虫（GPTBot / ClaudeBot / PerplexityBot）在抓取页面时完全不会「感知」到这个问题，它就不该单独成卡**。安全响应头就属于此类——GPTBot 不会因为缺 HSTS 就少引用你一段文字

**例外**：若该类问题被行业普遍认定为信任信号（如 HTTPS 本身缺失、明确的钓鱼/仿冒风险、严重的混合内容警告），仍可单独成卡，但 severity 上限为 Medium。

## finding 根因归并规则（防拆分过细）

写 `findings` 数组前，必须先做**根因聚类**：

1. 把候选问题列成草稿，每条标注「根因」（缺什么 / 错什么 / 多什么）
2. **同根因必须合并**：若两条草稿的根因相同（例：「主页缺第三方外链」与「主页无权威引用」），必须合并为一条 finding，**不允许拆成两条不同 severity**
3. **现象与测量必须合并**：若两条草稿是同一根因的「现象」与「测量」（例：「citability 低」是现象，「具体数字=0」是测量），必须合并——把测量数据写进 `evidence` 字段，不要单独立一条
4. **依赖链不拆**：若 B 是 A 的依赖（例：ItemList 依赖 Product schema），它们仍是两条独立 finding（修复动作不同），但 severity 应保持一致或 B ≤ A——**禁止** A 标 Critical 而 B 标 Low 这种「前置比后续还轻」的倒挂

**数量约束**：`findings` 数组总数期望 **6-10 条**；>12 条说明拆分过细，<5 条说明归并过度或漏诊。

## 协议权重表（GEO 原生 > SEO 原生 > 通用 Web）

评分与定级时，按协议与 GEO 的相关度加权：

| 协议类别 | 内容 | GEO 权重 |
|---|---|---|
| **GEO 原生协议** | llms.txt / llms-full.txt、JSON-LD Schema、Article.datePublished / `<time>` 标签、sameAs | **最高**——AI 引擎专用，直接影响引用与理解 |
| **SEO 原生协议** | robots.txt / sitemap.xml、canonical / hreflang、Open Graph / Twitter Card | **中等**——GEO 沿用，间接影响 |
| **通用 Web 协议** | 安全响应头（HSTS / CSP / X-Frame-Options）、性能优化（图片压缩 / 缓存 / CDN）、favicon / manifest.json | **低**——AI 爬虫不读或弱相关 |

**结构性缺陷放大效应**：若站点存在特定结构性缺陷，对应 GEO 原生协议的价值会**显著放大**，评分与定级时应上调一档。典型放大场景：
- URL 层级混乱（文章与产品混在根路径）→ llms.txt 的价值从 Medium 升到 High，因为它能显式告诉 AI 页面类型分类，弥补 URL 缺陷
- 多语言站点 hreflang 缺失 → canonical 与 sitemap 的价值放大
- 全文动态渲染（CSR）无 SSR → robots.txt 与 sitemap 的兜底价值放大

发现放大场景时，必须在 finding 的 `evidence` 或 `impact` 字段中**显式说明**「因站点存在 X 结构性缺陷，本协议的价值被放大」。

## finding 原子性规则（防打包时间窗差异巨大的工作）

一条 finding 必须是**原子级**的：

- **修复路径是单一动作**：可以由一个负责人在一个时间窗内完成
- **若草稿 finding 的 `fix` 字段需要写「①...②...③...」且 ① ② ③ 的耗时差异超过 5 倍**，说明这条 finding 应该拆开

**典型拆解示例**：

❌ 错误打包：
> 「sameAs=0 + Wikipedia/Wikidata 无词条」标 Critical（修复需 1 小时到 6 个月不等）

✅ 正确拆分：
> - 「Organization.sameAs 数组为空」severity Critical（小时级修复，依赖 Organization schema）
> - 「缺第三方平台认领（LinkedIn / YouTube / Crunchbase）」severity High（天级，含平台审核）
> - 「无 Wikidata / Wikipedia 词条」severity Medium（月级长期，需先积累第三方报道）

**禁止把「本周能修完」与「半年才能修完」的工作打包到同一条 finding**——这会让 P0 桶混入实际无法在本窗口完成的任务，给客户错误的预期。

## 评分纪律

1. **每条 Critical/High finding 必须引用真实观测值**（HTTP 状态、相似度、词数、schema 缺失项），不编造。
2. **枚举值用英文**：severity ∈ {Critical, High, Medium, Low}；confidence ∈ {Confirmed, Likely, Hypothesis}；layer ∈ {domain, template, editorial, page}。
3. **发现归并到正确层级**：跨页面复现的问题写 domain/template 层，不逐 URL 罗列。
4. **未测量的项写"未测量"**，不猜值。
5. quick_wins ≤ 4 小时可完成；medium_term 1-5 天；strategic 数周持续投入。
6. 修复建议要具体到可执行（"在 Cloudflare 白名单放行 GPTBot"，不是"优化爬虫配置"）。
6.1 **sameAs 评分边界**：sameAs 完整性仅对 Organization 强制（品牌实体对齐外部账号是品牌权威的核心信号）。对 Person：仅当作者是公开身份的外部专家/记者/行业 KOL（有 LinkedIn/Twitter/个人主页/Wikipedia 等真实可验证锚点）时才评分 sameAs；若作者为企业内部编辑/内容团队/匿名笔名/AI 生成占位名（无公开社交足迹），**不评分、不写 finding**——硬造 sameAs 反而会被 AI 反作弊判定为虚假身份。此时更合理的建议是把 `Article.author` 改为 `@type: Organization`（品牌作为发布者）或「编辑团队」实体。
7. **每个维度的 `note` 必填且 ≤120 字**——雷达图 tooltip 与「各维度得分理由」折叠面板都依赖这个字段。note 应说明：得分依据的关键实测值 + 主要扣分点。例：「首页 6 段 3F/2D，统计密度与独家信号几乎全为 0，无任何段落达到可直接引用标准」。空 note 会让报告该维度显示"未提供评分理由"。

## quick_wins 入选与排序规则（硬性约束）

`quick_wins` 数组顺序 = 报告 P0 视图的展示顺序，渲染层**不会重排**，agent 写错客户就看错。

**入选门槛（两条同时满足）**：

a. `effort ≤ 4 小时`
b. 对应的 finding `severity ∈ {Critical, High}`；Medium 级仅当 quick_wins 不足 3 条时按 finding severity 顺序补足。**严禁**用 Medium/Low 级任务把 Critical/High 级任务挤出 quick_wins。

**数组内部排序（三键降序，依次比较）**：

1. **finding severity 权重**：修复 Critical 的 > 修复 High 的 > 修复 Medium 的
2. **解锁作用**：能解锁后续任务的排前。典型前置链：`Organization schema → Product schema → ItemList`、`Organization → Article.publisher`、`Organization → sameAs 配置`。如果一条 quick_win 是其他 quick_win 或 medium_term 任务的前置依赖（提供 `@id`、提供模板改动基础），它必须排前
3. **修复成本**：前两项相同时，耗时短的排前

**禁止情形**：把「30 分钟能做完但只对应 Medium finding」（例如补安全响应头、写 llms.txt）排在「2 小时做完但对应 Critical finding」（例如注入 Organization schema）前面。耗时不是排序的主键，severity 才是。

**自检**：写完 quick_wins 后逐条标注「本条修复对应 findings 数组中第几条、severity 是什么」；若数组中存在 Medium 而 findings 里仍有 Critical/High 未被 quick_wins 或 medium_term 覆盖，说明排错，重做。

## medium_term 依赖拓扑排序规则（硬性约束）

`medium_term` 数组顺序 = 报告 P1 视图的展示顺序。Agent 必须显式建模任务间依赖，不能平行罗列。

**排序规则（依次比较）**：

1. **前置依赖**：被其他任务依赖的先做。常见依赖链：
   - Organization schema → Product schema（`brand.@id` 引用）→ ItemList（`itemListElement[].@id` 引用）
   - Organization schema → Article schema（`publisher.@id` 引用）
   - Organization schema → sameAs 配置（sameAs 是 Organization 的字段）
   - 作者身份确认 → 内容改写（内容里要署真实作者）
2. **finding severity**：无依赖关系时按对应 finding severity 降序
3. **同 severity 时按工作量升序**（小工作量先释放价值）

**依赖标注**：每个 action 的 `action` 字段末尾用「（前置：无）」「（前置：Organization schema 已就位）」「（前置：产品页 Product schema 已部署）」等形式标注，渲染层会原样展示，便于客户排期。

**拓扑自检**：写完后逐条检查——是否存在 A 任务依赖 B 但 A 排在 B 前面？若是，交换位置。最常见的错误：把 ItemList 排在 Product schema 前面、把 sameAs 配置排在 Organization schema 前面、把内容改写排在作者身份确认前面。

## Critical finding 覆盖性自检（硬性约束）

写完 `quick_wins` + `medium_term` + `strategic` 后，**必须**做一次反向覆盖校验：

1. 列出 `findings` 数组中所有 `severity = Critical` 的条目
2. 对每条 Critical finding，确认它能在 `quick_wins` 或 `medium_term` 里找到**明确对应的修复 action**（在 action 文案里能找到该 finding 的核心关键词）
3. 若某 Critical finding 完全没被任何行动覆盖 → 行动方案有漏洞，**必须补**，不允许「发现了但不给方案」
4. 若某 Critical finding 只能进 strategic（长期工作）→ 在 `impact_estimate` 开头一句话里显式说明「Critical finding X 因需要 N 周投入而归入 strategic」，不允许静默降级

**输出前最后一次自检**（在 JSON 写入文件前在脑里过一遍）：

- [ ] quick_wins 第 1 条是不是 Critical？
- [ ] quick_wins 里有没有 Medium 排在 Critical/High 前面？
- [ ] medium_term 里前置任务是否都排在被依赖任务前面？
- [ ] 每条 Critical finding 是否都有对应 action？
- [ ] 任务数组里是否每个 action 都能在 findings 里找到对应的 finding（不允许「无中生有」的 action）？

任何一条不过，回到对应步骤重做，不要带着错序的数组进 Phase 3。

## Schema 状态四态处理（重要）

`schema.json` 的 `inventory[*].status` 现在有 4 种状态，agent 必须分别处理：

| 状态 | 含义 | agent 行动 |
|---|---|---|
| `Present` | 检测到合法 JSON-LD | 正常评分；如有 `field_issues` 列入 finding |
| `Broken` | **存在但损坏**（JSON 语法错误 / HTML 实体污染 / 未转义换行 / 非法注释） | evidence 引用 `error` 与 `raw_preview`；fix 必须给具体修复代码；**研判影响边界（区分单块局部损坏还是全站崩溃）**，避免机械化夸大 |
| `Missing` | 采样范围内应出现但未标注 | 按现状处理（建设性 finding） |
| `NotSampled` | 该类型宿主页不在采样范围 | 不评分、不写 finding |

### Broken 处理流程与定级规范（硬性要求）

发现 `Broken` 时，**必须**：
1. 用 Bash 重新抓取该类型所在的页面（首页 / 文章页 / 产品页）
2. 用 grep / python 提取原始 `<script type="application/ld+json">` 块，检查该页面共有几个独立 `<script>` 标签
3. 用 `json.loads()` 验证具体错误位置与语法问题（如「内嵌 // 注释」、「未转义换行符」、「HTML 实体污染」）
4. **研判影响范围与独立性（防夸大误报）**：
   - 检查同页其他 `<script type="application/ld+json">` 块是否能够正常解析？
   - 若损坏的仅是其中一个 Block（例如包含 WebSite/BreadcrumbList），而同页独立的 NewsArticle、FAQPage、Product 块仍能正常被解析，**禁止在 evidence/impact 中断言「全站 Schema 崩溃」或「大模型完全无法解析任何内容」**，必须客观阐述为：「第 N 个 Script 块（包含 X 实体）因语法错误被解析器废弃，同页独立的 Y 实体依然可解析，但存在实体悬空引用/GSC 报警风险」。
5. `fix` 给出**可直接落地的代码片段**（例如 Liquid/PHP 模板清洗代码，或删除 `//` 注释的标准 JSON）
6. **severity 判定原则**：
   - 若损坏的 Block 包含全站级主核心实体（如 Organization / WebSite）导致核心实体断链，且修复极为简单（分钟/小时级，如删除几行注释），应作为 **P0 Quick Win（Critical 或 High）**，理由是「修复成本极低，却能立即消除 GSC 报警并补齐实体链」，而非「灾难性阻断」。
   - 若损坏的仅是非核心辅助块（如某一局部 BreadcrumbList），且不阻塞其他实体，severity 定为 **Medium 或 High**。

### field_issues 处理

`schema.json.field_issues` 列出 JSON-LD 字段级质量问题（HTML 实体编码、HTML 标签、字段缺失等）。每条 issue 应转化为一条 Medium/High 级 finding，evidence 引用 `sample` 字段的实际值。

### 电商站点首页推荐产品（Featured Products）与 ItemList Schema 检查规范（重要）

当站点为电商站点（Shopify / WooCommerce / DTC 品牌站）时，Agent **必须专项检查首页是否正确输出了产品推荐聚合层 Schema**：

1. **核对首页视觉内容与结构化数据**：
   - 检查首页 `fetch.json` 正文中是否包含「热销产品（Best Sellers）」、「主推系列（Featured Products）」、「选购指南（Find the Right One）」等推荐模块；
   - 检查首页的 `structured_data` 是否部署了 `@type: "ItemList"`（列表）或轻量产品引用。
2. **研判对 AI Shopping 的业务影响**：
   - 若首页有主推产品推荐区，但全站首页完全缺少 `ItemList` 结构化数据：
     - **业务痛点**：AI Shopping Agent（如 ChatGPT Search 购物推荐、Perplexity 购物助手等）在浅层抓取全站最高权重页（首页）时，只能拿到纯视觉轮播碎片，无法在单次请求中快速提取主推产品清单与型号映射，降低了品牌核心产品在 AI 购物问答中的推荐权重。
     - **判定级别**：应作为一条 **High 或 Medium 级别 finding**（layer: `template`）。
     - **修复建议（fix）**：给出在首页推荐组件（如 `featured-products.liquid` / `featured-collection.liquid`）中注入 `@type: "ItemList"` JSON-LD 的标准代码示例（使用 `itemListElement` 包装轻量 `Product` 引用与 `@id`，切忌直接堆砌不规范的独立 Product）。

### 行业自适应外链与权威度研判规范（跨行业通用）

**硬约束（误报防线）**：
- **禁止**在 finding / evidence 里写「主页未链向任何权威外部平台」「媒体 Logo 墙均为图片无文字链接」等断言，**除非 `signals.json.main_page.content.links.value.citation_count == 0`**。
- `is_high_trust_domain` 字段只是粗筛，**不能作为权威度结论**——TechRadar / PCWorld / CNET / Wirecutter / Tom's Guide / PCMag 等是否在列表中，由你按行业自行判断，不要依赖该布尔值。
- 若 `citation_count > 0`，finding 的正确写法是「外链锚文本/上下文不充分」（如锚文本全是「Learn More」、未在正文提炼评测结论），**不是「未链向权威平台」**。

`signals.json` 已把外链结构化分类为 `citations`（独立第三方内容外链，已自动剔除社媒与支付工具等噪音）。**Agent 必须根据本站所属的实际行业，自主评估这些外链的权威度**：

1. **识别站点行业**：根据首页/产品内容判断垂直赛道（如科技数码、精品咖啡、工业照明、医疗健康、B2B 软件等）。
2. **LLM 知识库自适应匹配**：
   - 科技数码类：识别 TechRadar / PCWorld / CNET / Wirecutter / Tom's Guide / PCMag 等；
   - 精品咖啡类：识别 SCA / Coffee Review / Sprudge / Perfect Daily Grind 等；
   - 工业制造类：识别 ISO / IEC / UL 认证 / 行业协会 / 垂直工业期刊等；
   - 医疗健康类：识别 PubMed / NIH / WHO / 知名医学期刊等；
   - B2B/SaaS 类：识别 Gartner / Forrester / G2 / Capterra / TechCrunch 等。
3. **结合段落上下文评估证据质量**：
   - 是否在正文中明确提炼了该第三方机构的评测结论/高光引语（Quote）；
   - 还是仅仅挂了一个空的链接或「Learn More」跳转（证据表达不充分）。

## evidence 与 fix 写作规范（客户可读性硬约束）

`evidence` 与 `fix` 字段是直接交付给客户阅读的，**禁止出现内部实现细节**——客户不知道、也不需要知道 `fetch.json`、`schema.json` 这些采集脚本的中间产物文件名。

**禁写**：`*.json` 文件名、内部字段名（`total_blocks` / `has_organization` / `sameas_count` / `headers_present` 等）、Python 数据结构字面量（`[]`、`{}`、`False`）。

**应写**：用「**动作 + 对象 + 观测值**」的自然语言——抓取了什么页面 / 接口，看到了什么具体现象。具体数值（HTTP 状态码、词数、百分比、HTTP 头名）保留，这些是可读证据。

### 路由与外部 URL 显式引用规范（重要）

当在 `evidence`（证据）或 `fix`（建议动作）中提及**具体页面、外部媒体报道、竞品参考或验证工具**时，**必须在名称后紧跟完整的有效 URL**（如 `TechRadar 评测报道（https://www.techradar.com/...）`、`Google 富媒体测试工具（https://search.google.com/test/rich-results）`）。

- **原因**：报告渲染引擎已支持自动链接解析，显式附带 URL 会自动生成可点击的新标签跳转链接，极大提升客户的排查与复核效率。
- **格式建议**：使用中文或英文括号将完整 URL 括起来，如 `媒体名（https://...）`，不要使用 markdown 链接语法 `[媒体](https://...)`（会被自动清洗转换）。

**正反对照**：

| ❌ 禁写 | ✅ 应写 |
|---|---|
| `schema.json 实测 total_blocks=0` | 抓取首页 + 3 篇新闻 + 3 个产品页共 7 页，全部未检测到任何 JSON-LD 结构化数据 |
| `仅包含 1 条指向独立权威媒体（TechRadar）的外链` | 正文仅包含 1 条指向独立权威媒体 TechRadar 报道（https://www.techradar.com/vehicle-tech/dash-cams/ready-for-a-summer-road-trip-botslabs-amazing-dashcams-are-on-sale-for-prime-day）的外链 |
| `用 Rich Results Test 验证` | 改完后使用 Google Rich Results Test（https://search.google.com/test/rich-results）验证 |
| `fetch.json：h1_tags=[]` | 首页 HTML 中未检测到任何 H1 标签；H2/H3 多处为空字符串 |
| `brand.json：wikipedia.has_wikipedia_page=false` | 通过 Wikipedia / Wikidata 官方 API 检索「品牌名」，均无词条 |
| `technical.json：headers_present=1/6` | 实测首页响应头：仅含 HSTS，缺 CSP、X-Frame-Options 等其余 5 项 |
| `pages.json：missing=['support','faq']` | 全站未发现「支持」与「FAQ」独立页面，页脚与导航均无对应入口 |

## 页面分析边界（硬性约束，防硬编码猜 URL）

7. **只分析 `work/*.json` 里真实采集到的页面**。功能页（Support/FAQ/Contact/About/Warranty）以 `pages.json` 为准：只在 `pages.json.found` 里出现、且 `status_code < 400` 的页面才可作为诊断对象。
8. **严禁自行拼 URL**（如 `域名+/support`、`域名+/pages/support`）。`pages.json.missing` 里列出的类型 = 站点未发布该类页面，应写成"未发布 Support 页 / 建议增设 FAQ"这类**建设性 finding**，绝不虚构一个页面去分析。
9. `pages.json.found[t]` 若带 `note`（发现的链接返回 4xx），按**缺失**处理，并在 finding 中引用真实状态码（如"Support 链接实际返回 404"）。

### FAQ 页诊断规则（防误报）

**独立 FAQ 页不是 GEO 的必要条件**。AI 检索（Google AIO / Perplexity / ChatGPT）认的是 `FAQPage schema`，不是「FAQ 页」这个 URL 形态。

10. **先查 schema.json 的 FAQPage 状态**：
    - `FAQPage: Present`（无论内嵌在博客/产品页还是独立页）→ AI 已可提取问答对，「缺独立 FAQ 页」**不构成 GEO finding**，不评分、不写。
    - `FAQPage: Missing` 且 `pages.json.missing` 含 `faq` → 可写 Medium 级建设性 finding，建议增设 FAQ 内容（独立页或内嵌模块均可）。
    - `FAQPage: Missing` 但博客/产品页正文明显有 Q&A 模块（仅缺 schema）→ 应写「为现有 Q&A 内容补 FAQPage schema」，而非「新建独立 FAQ 页」。
    - `pages.json.found.faq` 存在但 `issues.json` 报 GEO-COV-008（FAQ 页有内容无 FAQPage schema）→ 直接引用该规则证据，finding 是「补标注」而非「建页面」。注意：FAQ 页可能位于 `/blogs/faq` 等非标准路径（Shopify 常见），discovery 已能识别，不要再自行拼 `/pages/faq` 猜测。

### Contact 页诊断规则（防误报）

**独立 Contact 页也不是 GEO 的必要条件**。AI 与 Google 看的是「联系方式是否可被结构化识别」，不是 URL 形态。

11. **先查 Organization schema 的 contactPoint 与首页/页脚/About 页正文**：
    - `Organization.contactPoint` 已配置（邮箱/电话/工单入口任一）→ 联系方式已被结构化，「缺独立 Contact 页」**不构成 GEO finding**，不评分、不写。
    - 首页/页脚/About 页正文有可见的联系邮箱/电话/表单（即便无 contactPoint schema）→ 降级为 Low 级建议「为现有联系方式补 contactPoint schema」，**不要求新建独立 Contact 页**。
    - 全站完全无任何联系方式（既无 schema 也无正文披露）→ 才写 Medium/High finding「缺失联系方式，E-E-A-T 信任分受损」，建议披露联系方式（独立页或页脚/About 内嵌均可）。

## 术语表（保持一致）

可引用性 / 爬虫可达性 / 实体图谱 / sameAs（保留）/ Schema（保留）/ E-E-A-T（保留）/ SSR（保留）/ 新鲜度 / 训练类爬虫 / 检索类爬虫
