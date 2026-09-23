# ARCHITECTURE_AUDIT.md — site-diagnosis-zh 架构审计

> 审计日期：2026-09-22 · 审计范围：全部 Python 源码（约 6000 行）、SKILL.md、prompts/、templates/、历史诊断产出（results/）
> 结论先行：**当前项目已经具备「确定性采集 + agent 判断 + 数据驱动渲染」的正确骨架，signals/analyzers 已经是规则引擎的雏形。主要问题不是方向错误，而是：引擎逻辑漏进了渲染层、数据模型不统一、重复抓取、硬编码残留、零测试。**

---

## 1. 当前架构

项目是「**双脑结构**」——脚本做确定性采集，agent（即 LLM）做评分与中文撰写：

```
URL
 │
 │  report.py collect                    【确定性，脚本】
 ▼
diagnose/ ──► work/*.json（fetch/robots/bots/citability/schema/brand/technical/articles/pages/signals/issues）
 │
 │  agent 读 prompts/score.md            【判断，agent】
 ▼
work/scores.json（六维评分 + 中文 findings + quick_wins/medium_term/strategic）
 │
 │  report.py render                     【确定性，脚本】
 ▼
render/build.py ──► scripts/report_builder.py ──► render/charts.py ──► Jinja2 ──► 单文件中文 HTML
 │
 │  agent 直接 Write（不经渲染层）         【判断，agent】
 ▼
GEO_EXECUTION_PLAN.md
```

五个阶段（SKILL.md 定义）：
- **Phase 0**：建 `results/<domain>-<ts>/` 输出目录
- **Phase 1**：`report.py collect` 确定性采集（10 个 JSON）
- **Phase 1.5**：动态 URL 站点由 agent 人工挑选文章/产品页（`agent_picks.json` → `fetch-sampled`）
- **Phase 2**：agent 按 `prompts/score.md` 评分，产出 `scores.json`
- **Phase 2.5**：agent 直接撰写 `GEO_EXECUTION_PLAN.md`（明确不走脚本）
- **Phase 3**：`report.py render` 渲染 HTML

## 2. 数据流明细

### 2.1 采集层（diagnose/）

| 模块 | 输入 | 输出 JSON | 职责 |
|---|---|---|---|
| `fetch.py` | URL | `fetch.json` | 首页抓取：title/meta/标题树/正文/JSON-LD（**4 级容错修复**：直接解析→HTML 实体→未转义换行→JS 注释→占位标记）/安全头/新鲜度/SSR 判定 |
| `sampling.py` | internal_links | （驱动 articles） | 文章页/产品页正则抽样（均匀取样） |
| `discovery.py` | URL + internal_links | `pages.json` | 功能页发现（Support/FAQ/Contact/About/Warranty），命中才抓，不猜路径 |
| `schema_check.py` | structured_data | `schema.json` | Schema 四态清单（Present/Broken/Missing/NotSampled）+ sameAs + 电商识别 |
| `robots.py` | URL | `robots.json` | robots.txt 解析 + AI 爬虫声明策略 + **sitemap 可达性分析** |
| `bots.py` | URL | `bots.json` | 12 个 AI 爬虫 UA 重放实测 + WAF 识别 + verdict 判定 |
| `citability.py` | URL | `citability.json` | 段落级可引用性五维评分（**独立再抓一次首页**） |
| `brand.py` | URL | `brand.json` | Wikipedia/Wikidata API 验证；Reddit/YouTube/LinkedIn 留给 agent 复核 |
| `technical.py` | URL + 安全头 | `technical.json` | 安全头矩阵 + HTTPS + llms.txt 探测 |
| `freshness.py` | （被 fetch 调用） | — | dateModified/`<time>`/Last-Modified 分级（fresh/aging/stale/very-stale） |

### 2.2 信号/分析层（diagnose/signals + diagnose/analyzers）

这是**规则引擎的雏形**，已具备正确的分层意识：

```
fetch_page 结果
   │
   ▼
signals/gatherer.py          纯事实提取，make_signal(value, location, raw) 统一契约
   ├─ meta_signals           title/description/og/canonical/h1/status
   ├─ author_signals         schema author / byline / About-the-Author / meta author
   ├─ entity_signals         Organization/Person/Product/Article 跨页聚合
   └─ content_signals        绝对化词 / 数字密度 / 外链三分（citations/same_brand/utility）
   │
   ▼  signals.json
analyzers/（@register 装饰器 + REGISTRY）
   ├─ consistency            作者署名跨位置一致性（含多页聚合为模板级问题）
   ├─ entity_graph           Organization 存在性/sameAs/@id 一致性/Product.brand/Person.worksFor
   ├─ coverage               H1 通用性/meta_description/Article.description/og/canonical
   ├─ argumentation          绝对化密度/第三方引用数/数字密度
   └─ authority              主页权威外链
   │
   ▼  issues.json（统一 make_issue 结构：category/kind/severity/title/evidence/impact/fix/confidence/layer）
```

### 2.3 评分层（agent）

- `prompts/score.md`（300 行评分卡）：六维评分依据、severity 三列判定矩阵、finding 准入门槛、根因归并、原子性规则、quick_wins 排序规则、medium_term 拓扑排序、Critical 覆盖性自检、Schema 四态处理、行业自适应外链研判
- 产出 `scores.json`：dimensions（六维 0-100 + note）/ findings（中文）/ quick_wins / medium_term / strategic / paragraph_hints / impact_estimate

### 2.4 报告层（render/ + scripts/）

| 模块 | 职责 |
|---|---|
| `render/build.py` | 加载 10 个 JSON → 中文伴生字段 → **schema 回退评分** → 维度分回退映射 → agent findings 与 analyzer issues 合并去重 → 调 report_builder → ReportData |
| `scripts/report_builder.py` | findings → ExecutiveSummary / TopPriorities / PageRecommendations / PriorityActions / Roadmap（30/60/90 天）/ chart_visibility；含文本重叠去重（`_is_overlapping`） |
| `render/charts.py` | ECharts 数据预计算 + `should_render_chart` 数据充分性判断 |
| `render/html.py` | Jinja2 渲染 + URL autolink + 内嵌原始 JSON |
| `templates/` | base.html.j2（42KB）+ 9 个 section 模板 |

## 3. 核心依赖

- **运行时**：requests / beautifulsoup4 / lxml / jinja2（4 个，无重型依赖，良好）
- **外部 API**：仅 Wikipedia/Wikidata 官方 API（确定性验证），其余平台由 agent 复核
- **ECharts**：CDN（可选 --offline 内联）
- **agent 环境**：Claude Code / Codex，agent 即 LLM（零 API key 设计）

## 4. 当前问题（按严重度排序）

### P0 — 架构性问题

1. **引擎逻辑漏进渲染层**（最严重）。`render/build.py` 承担了太多诊断职责：
   - `_schema_score()`：schema 维度评分规则（关键类型覆盖 60 分 + sameAs 阶梯 40 分）——**这是 ScoreEngine 的活**
   - `_normalize_schema()`：旧数据格式迁移——**兼容逻辑应在采集层出口或独立 migration**
   - 新鲜度跨页回退合并、维度 fallback 映射（content 用 citability 代理、platform 用 bots 代理）——**评分规则**
   - `_format_analyzer_evidence()`：把结构化 issue 转中文叙述——**这是 finding 规范化的活，且内含硬编码**（见问题 4）

2. **数据模型不统一、重复定义**：
   - `TopPriority / PageRecommendation / PriorityAction / RoadmapPhase / ExecutiveSummary` 在 `render/model.py` 和 `scripts/report_builder.py` **各定义了一遍**（字段还不完全一致，report_builder 版多了 `finding_index`，且 PriorityAction 有个重复的 `owner_zh` 字段）
   - Finding 有两种形态：agent 的自由文本 finding 和 analyzer 的结构化 issue，靠 `t[:8] in at`（标题前 8 字包含匹配）去重——**极其脆弱**
   - issue 有 `kind`（机器可读）但 finding 没有稳定 ID；rule 没有 id/weight 元数据

3. **重复抓取，无缓存**：
   - 首页被 fetch.py 抓 1 次、citability.py **再抓 1 次**、bots.py 抓 1 次基线 + 12 次 UA 重放
   - sitemap.xml 被 `robots.py:_parse_sitemap_urls` 和 `discovery.py:fetch_sitemap_urls` **两套独立实现各解析一遍**（代码近乎复制）
   - 同一 URL 改评分逻辑再跑，全部重新抓一遍——`results/*/work/*.json` 已是天然 fixture 但未被利用

### P1 — 工程债

4. **硬编码残留（客户数据泄漏进通用代码）**：
   - `coverage.py:176`：`p.replace('https://www.botslab.com','')` —— **特定客户域名写死在聚合逻辑里**
   - `build.py:_format_analyzer_evidence` 权威外链分支硬编码 techradar/pcworld/trustedreviews——特定行业
   - 权重散落至少 5 处：`render/build.py:WEIGHTS`（五维）、`brand.py:WEIGHTS`、`technical.py:HEADERS` 分值、`bots.py`（0.5/0.35/0.15）、`citability.py:DIM_MAX`
   - 阈值散落：抽样 5 篇/3 个、limit=500/200、90/365/730 天、相似度 0.4、密度 1.5/500 词等

5. **零测试、零 fixture**：`results/` 下有 10 个真实站点的完整 work/*.json（含 broken JSON-LD、动态 URL、WAF 拦截等真实案例），是现成的回归测试原料，完全未利用。

6. **入口与路径 hack**：`report.py:19` 与 `build.py:468` 两处 `sys.path.insert`；`scripts/report_builder.py` 无法作为正常模块导入。无 pyproject.toml。

7. **重复/重叠的检查逻辑**：
   - Organization 缺失：schema_check（inventory Missing）⊃ entity_graph（homepage_missing_organization）⊃ build.py 兜底 finding 三处都可能产出
   - meta_description 缺失：coverage 分析器 vs fetch 已提取
   - 没有单一事实来源告诉 agent「这个问题已经被确定性规则覆盖了」

8. **配置缺失**：timeout（10/15/30 秒散落各处）、UA 列表、抽样上限、功能页关键词表全部是代码内常量。

### P2 — 设计层面待明确

9. **ScoreEngine 不存在**：确定性评分只有 build.py 里的 4 个回退公式；真正的六维评分是 agent 按 prompt 执行的——这是设计选择（双脑），但**确定性部分（schema 覆盖率、爬虫可达率、citability 均分）完全可以脚本化打底**，让 agent 只做加减分与撰写，减少每次诊断的评分漂移。

10. **issues.json 半废弃**：分析器产出的结构化 issues 在 render 层被转成自然语言 findings 后与 agent findings 混合，但 agent 在 Phase 2 评分时**并不读 issues.json 的约定不明确**（score.md 未提及 issues.json），两条诊断链路事实脱节。

11. **llms.txt 只有探测没有解析**：technical.py 只判断存在性，无内容质量分析（对比 geo-llmstxt skill 的能力差距）。

## 5. 可复用能力（重构时保留的宝贵资产）

| 资产 | 位置 | 为什么宝贵 |
|---|---|---|
| JSON-LD 4 级容错修复 | `fetch.py:61-169` | 在真实客户站上打过仗（HTML 实体/未转义换行/JS 注释都遇到过），含 Broken 占位与错误定位 |
| AI 爬虫名单与分类 | `crawlers.py` | 12 个 UA + 四类（live-retrieval/search-index/traditional-search/training）|
| UA 重放 + verdict | `bots.py` | 声明≠实测的方法论，WAF 识别、content_stripped 检测 |
| 可引用性五维评分 | `citability.py` | 中英通用、确定性、有 DIM_META 改写提示体系 |
| Schema 四态模型 | `schema_check.py` | Present/Broken/Missing/NotSampled 是防误报的关键设计 |
| signals 契约 | `signals/__init__.py` | make_signal(value/location/raw) 已是 Evidence 的雏形 |
| analyzer 注册表 | `analyzers/__init__.py` | @register + REGISTRY + 单分析器失败降级——**离 Rule Engine 只差 id/weight 元数据** |
| 重叠去重算法 | `report_builder.py:_is_overlapping` | 包含度签名去重，解决 finding/action 重复 |
| 图表充分性判断 | `charts.py:should_render_chart` | 数据不足不画图的纪律 |
| 中文伴生字段模式 | `build.py:ZH_*` | 机器值英文 + 展示值中文的干净分离 |
| 功能页发现纪律 | `discovery.py` | 「只诊断真实抓到的页，绝不拼路径」——防幻觉设计 |
| prompts/score.md | 300 行评分卡 | severity 三列矩阵、根因归并、原子性等规则是大量实战教训的结晶 |
| 真实诊断语料 | `results/*/work/` | 10 个站点完整 JSON，回归测试 fixture 来源 |

## 6. 建议重构方向（对应任务书 21 条的落地映射）

### 原则：演进式重构，不重写

这个项目最危险的改法是「按目标目录结构机械拆文件」。正确做法是**保留双脑设计与现有采集能力，分层抽换**。

### Phase 1 — 数据模型与规则引擎地基（不动 CLI 行为）

1. **建 `core/models.py`**：统一 `Page / SiteContext / Evidence / Finding / Recommendation / DiagnosisResult`。删除 model.py 与 report_builder.py 的双份 dataclass 定义，report_builder import 自 models。
2. **analyzers/ → checks/ 演进**：给现有 5 个分析器补 `id / category / severity / weight` 元数据（如 `entity_graph.organization_no_sameas` → `GEO-ENT-002`）。Registry 模式保留。Finding 增加稳定 `id` 与 `rule_id`。
3. **修掉 P0 硬编码**：coverage.py 的 botslab.com 域名、build.py 的行业媒体清单，改为配置驱动。

### Phase 2 — 引擎与渲染解耦

4. **建 `scoring/engine.py`**：把 `_schema_score`、维度 fallback 映射、新鲜度跨页合并从 build.py 抽出。确定性评分脚本化打底，agent scores.json 变为「override + 撰写」层。
5. **建 `recommendations/engine.py`**：把 report_builder 的 priority_actions / roadmap / page_recs 生成逻辑吸收进来（这是规则部分）；agent 的 GEO_EXECUTION_PLAN.md 保持 agent 撰写不变。
6. **build.py 瘦身**为纯装配：load → normalize（迁移逻辑独立成 `core/migrations.py`）→ merge → ReportData。

### Phase 3 — 采集层治理

7. **建 `crawler/cache.py`**：memory cache 优先，filesystem cache 可选（key = URL+UA 哈希）。所有 get() 走缓存。
8. **合并 sitemap 双实现**：robots.py 与 discovery.py 共用 `parsers/sitemap.py`。
9. **citability 复用 fetch 结果**而非重新抓取（接受 page dict 参数，URL 模式仅作 CLI 便利）。
10. **建 `config/default.yaml`**：权重、阈值、超时、爬虫名单、功能页关键词全部入配置。

### Phase 4 — 测试与回归

11. **fixtures/ 从 results/ 提取**：broken_jsonld（edgelight blog 案例）、动态 URL 站（newoxy 案例）、WAF 拦截案例等。
12. **tests/**：优先 JSON-LD 4 级修复、sitemap 解析、schema 四态、citability 中英词数、score 合成、issues→findings 合并去重。

### Phase 5 — CLI 与文档

13. `pyproject.toml` + `geo-diagnose` entry point；`report.py` 保留为兼容薄壳。
14. **SKILL.md 重写为 Agent 使用说明**（Purpose/When/Input/Workflow/Output schema/Error handling/Extension）。

### 明确不做（或推迟）

- **不把 agent 评分全部脚本化**：六维评分的 content/brand 维度需要语义判断，双脑是核心设计。只把确定性部分打底。
- **不为 GEO_EXECUTION_PLAN.md 写生成器**：SKILL.md 已明确这是判断工作（任务合并/依赖排序），硬编码规则套不上不同站点。
- **不引入重型框架**（pydantic 可选但非必须；当前 4 个依赖的轻量性是优势）。
- **不做 PDF/Dashboard**：Renderer 接口稳定后自然可扩，不在本轮。

## 7. 暂时不要改动的部分

| 部分 | 原因 |
|---|---|
| `prompts/score.md` 的评分纪律内容 | 实战教训结晶，重构只改它引用的文件路径与字段名，不改评分哲学 |
| `templates/` 视觉与信息架构 | 客户交付物形态已验证（暖纸风格、3 Group 结构），重构不动展示层 |
| GEO_EXECUTION_PLAN.md 的 agent 直写模式 | 设计决策明确（Phase 2.5 注释），任务粒度规则只能由 LLM 执行 |
| `results/` 历史产出 | 只读语料，是 fixtures 来源 |
| `report.py collect/fetch-sampled/render` 三个子命令的行为契约 | agent 已按 SKILL.md 形成肌肉记忆，CLI 兼容性必须保持 |
| `fetch.py` JSON-LD 修复的具体正则与顺序 | 每一级修复都对应真实案例，改动需要 fixture 保护先行 |
| 双脑分工哲学 | README/SKILL 的「脚本做确定性的事，agent 做判断与写作」是项目灵魂 |

## 8. 风险登记

| 风险 | 缓解 |
|---|---|
| 重构改变诊断产出（同一 URL 前后报告不一致） | Phase 4 的 fixture 回归测试先行；每个 Phase 结束用 results/ 历史 work/ 重放 render 对比 |
| checks/ 拆分时丢失 analyzer 现有的多页聚合逻辑（consistency 的模板级合并、coverage 的 _aggregate_by_kind） | 聚合器作为 checks 框架的内置能力保留，不散落到各 rule |
| agent scores.json 与确定性打分并存的优先级混乱 | 明确定义：确定性分为 floor/baseline，agent note 为展示，scores.json 覆盖时记录 override 来源 |
| 配置化过度（把业务判断也搬进 yaml） | 配置只收「数值与名单」，规则逻辑留在代码 |
