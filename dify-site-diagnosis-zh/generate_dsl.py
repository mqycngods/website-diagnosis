#!/usr/bin/env python3
"""生成 workflow.dsl.yaml —— site-diagnosis-zh 的 Dify 工作流。

节点拓扑（串行，模仿 dify-agent-a-v2）：
  start → 0.读取配置 → 1.核心抓取 → 2.爬虫与引用 → 3.页面扫描
        → 4.LLM挑页 → 5.补抓重建 → 6.综合分析 → 7.LLM评分
        → 8.校验修正 → 9.保存评分 → 10.LLM执行计划 → 11.渲染报告 → end

无文件系统设计：服务端按 run_id 内存暂存采集数据（TTL 2h），
Dify 节点间只传紧凑摘要；渲染端点直接返回 HTML/MD 字符串。
"""
import yaml

TS = 1731000000000  # 节点 id 基数

# ── 节点代码 ─────────────────────────────────────────────────

NODE0_CODE = '''import uuid
from urllib.parse import urlparse

def main(biz_api_base: str, geo_api_token: str, url: str) -> dict:
    u = url if url.startswith("http") else f"https://{url}"
    domain = urlparse(u).netloc.replace("www.", "").replace(":", "_")
    return {
        "biz_api_base": biz_api_base,
        "geo_api_token": geo_api_token,
        "run_id": uuid.uuid4().hex[:12],
        "domain": domain,
        "url": u,
    }'''


def _http_node(tool: str, extra_payload: str, success_return: str,
               empty_fields: str, timeout: int, retries: int = 3) -> str:
    """生成「带指数退避重试的 POST /api/v1/tools/<tool>」节点代码。"""
    return f'''import requests
import time

def main(run_id, {extra_payload["sig"]}biz_api_base, geo_api_token):
    headers = {{"Authorization": f"Bearer {{geo_api_token}}"}}
    endpoint = f"{{biz_api_base}}/api/v1/tools/{tool}"
    payload = {{"run_id": run_id, {extra_payload["body"]}}}
    empty = {{"ok": False, "error": "", {empty_fields}}}
    last_error = ""
    for attempt in range({retries}):
        try:
            resp = requests.post(endpoint, json=payload, headers=headers, timeout={timeout})
            result = resp.json()
            if not result.get("ok"):
                empty["error"] = result.get("error", "unknown")
                return empty
            d = result.get("data", {{}})
            return {success_return}
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_error = f"attempt {{attempt+1}}/{retries}: {{type(e).__name__}}: {{e}}"
            if attempt < {retries - 1}:
                time.sleep(2 ** attempt)
        except Exception as e:
            empty["error"] = f"{{type(e).__name__}}: {{e}}"
            return empty
    empty["error"] = f"重试 {retries} 次仍失败: {{last_error}}"
    return empty'''


NODE1_CODE = _http_node(
    "diagnosis_fetch_core",
    {"sig": "url, ", "body": '"url": url'},
    '{"ok": True, "error": "", "fetch_summary": d.get("fetch_summary", {}), '
    '"robots": d.get("robots", {}), "technical": d.get("technical", {})}',
    '"fetch_summary": {}, "robots": {}, "technical": {}',
    timeout=180)

NODE2_CODE = _http_node(
    "diagnosis_probe_bots",
    {"sig": "url, ", "body": '"url": url'},
    '{"ok": True, "error": "", "bots": d.get("bots", {}), '
    '"citability_summary": d.get("citability_summary", {})}',
    '"bots": {}, "citability_summary": {}',
    timeout=240)

NODE3_CODE = _http_node(
    "diagnosis_scan_pages",
    {"sig": "url, ", "body": '"url": url'},
    '{"ok": True, "error": "", "internal_links_json": d.get("internal_links_json", "[]"), '
    '"sampled_articles": d.get("sampled_articles", []), '
    '"sampled_products": d.get("sampled_products", []), '
    '"pages_summary": d.get("pages_summary", {}), "schema": d.get("schema", {})}',
    '"internal_links_json": "[]", "sampled_articles": [], "sampled_products": [], '
    '"pages_summary": {}, "schema": {}',
    timeout=240)

NODE5_CODE = _http_node(
    "diagnosis_rescan_pages",
    {"sig": "articles, products, ", "body": '"agent_picks": {"articles": articles, "products": products}'},
    '{"ok": True, "error": "", "schema": d.get("schema", {}), '
    '"sampled_articles": d.get("sampled_articles", []), '
    '"sampled_products": d.get("sampled_products", [])}',
    '"schema": {}, "sampled_articles": [], "sampled_products": []',
    timeout=240)

NODE6_CODE = _http_node(
    "diagnosis_analyze",
    {"sig": "url, ", "body": '"url": url'},
    '{"ok": True, "error": "", "brand": d.get("brand", {}), '
    '"issues": d.get("issues", {}), "signals_summary": d.get("signals_summary", {}), '
    '"baseline_scores": d.get("baseline_scores", {})}',
    '"brand": {}, "issues": {}, "signals_summary": {}, "baseline_scores": {}',
    timeout=120)

NODE8_CODE = '''import json

DIM_KEYS = ["citability", "brand", "content", "technical", "schema", "platform"]
SEVERITIES = {"Critical", "High", "Medium", "Low"}
CONFIDENCES = {"Confirmed", "Likely", "Hypothesis"}
LAYERS = {"domain", "template", "editorial", "page"}

def _parse(v, default):
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return default

def main(description, dimensions_json, findings_json, quick_wins_json,
         medium_term_json, strategic_json, impact_estimate, paragraph_hints_json,
         baseline_scores):
    fixes = []
    scores = {}
    scores["description"] = description if isinstance(description, str) else str(description or "")

    dims = _parse(dimensions_json, {})
    if not isinstance(dims, dict):
        dims = {}
        fixes.append("dimensions 不是对象，已重置")
    out_dims = {}
    for k in DIM_KEYS:
        d = dims.get(k) or {}
        if not isinstance(d, dict):
            d = {}
        score = d.get("score")
        try:
            score = int(round(float(score)))
        except (TypeError, ValueError):
            base = (baseline_scores or {}).get(k) or {}
            score = base.get("score", 0)
            fixes.append(f"dimensions.{k}.score 缺失，回退 baseline")
        out_dims[k] = {"score": max(0, min(100, score)),
                       "note": str(d.get("note") or "")[:200]}
    scores["dimensions"] = out_dims

    def _list(name, raw):
        v = _parse(raw, [])
        if not isinstance(v, list):
            fixes.append(f"{name} 解析失败，已置空")
            return []
        return [x for x in v if isinstance(x, dict)]

    findings = _list("findings", findings_json)
    for f in findings:
        if f.get("severity") not in SEVERITIES:
            f["severity"] = "Medium"
            fixes.append(f"finding severity 修正: {str(f.get('title', '?'))[:20]}")
        if f.get("confidence") not in CONFIDENCES:
            f["confidence"] = "Likely"
        if f.get("layer") not in LAYERS:
            f["layer"] = "domain"
    scores["findings"] = findings
    scores["quick_wins"] = _list("quick_wins", quick_wins_json)
    scores["medium_term"] = _list("medium_term", medium_term_json)
    scores["strategic"] = _list("strategic", strategic_json)
    scores["impact_estimate"] = impact_estimate if isinstance(impact_estimate, str) else str(impact_estimate or "")
    scores["paragraph_hints"] = _list("paragraph_hints", paragraph_hints_json)

    return {"ok": True, "error": "",
            "scores_json": json.dumps(scores, ensure_ascii=False),
            "fixes_applied": fixes}'''

NODE9_CODE = '''import requests
import time
import json

def main(run_id, scores_json, biz_api_base, geo_api_token):
    headers = {"Authorization": f"Bearer {geo_api_token}"}
    endpoint = f"{biz_api_base}/api/v1/tools/diagnosis_save_scores"
    try:
        scores = json.loads(scores_json)
    except Exception as e:
        return {"ok": False, "error": f"scores_json 非法 JSON: {e}"}
    payload = {"run_id": run_id, "scores": scores}
    last_error = ""
    for attempt in range(3):
        try:
            resp = requests.post(endpoint, json=payload, headers=headers, timeout=30)
            result = resp.json()
            if not result.get("ok"):
                return {"ok": False, "error": result.get("error", "unknown")}
            return {"ok": True, "error": ""}
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_error = f"attempt {attempt+1}/3: {type(e).__name__}: {e}"
            if attempt < 2:
                time.sleep(2 ** attempt)
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": False, "error": f"重试 3 次仍失败: {last_error}"}'''

NODE11_CODE = _http_node(
    "diagnosis_render",
    {"sig": "url, domain, ", "body": '"url": url, "domain": domain'},
    '{"ok": True, "error": "", "geo_score": d.get("geo_score", 0), '
    '"label_zh": d.get("label_zh", ""), "report_html": d.get("report_html", ""), '
    '"report_markdown": d.get("report_markdown", ""), '
    '"download_url_html": d.get("download_url_html", ""), '
    '"download_url_md": d.get("download_url_md", "")}',
    '"geo_score": 0, "label_zh": "", "report_html": "", "report_markdown": "", '
    '"download_url_html": "", "download_url_md": ""',
    timeout=120)

# ── LLM Prompts ──────────────────────────────────────────────

LLM4_SYS = '''你是 GEO 诊断的页面采样专家。任务：从官网内部链接清单中挑出最有代表性的文章页与产品/服务详情页，供后续抓取采样。

规则：
1. 文章页识别：链接文本通常是具体标题（新闻/博客/资讯/案例），URL 常含 /news/ /blog/ /article/ /post/ 或 id=N 参数。
2. 产品页识别：链接文本通常是具体产品名/型号，URL 常含 /product/ /products/ /item/ /shop/ 或 id=N。
3. 不要选：导航页、列表页、「更多」「了解更多」「View all」类通用按钮、分页链接、纯锚点链接、外部链接。
4. 数量：文章 3-5 篇、产品 2-3 个。尽量覆盖不同栏目，避免同一栏目重复。
5. 自动采样结果是默认值：若合理请直接保留（逐字抄入输出），再按需补充或替换。
6. 若确实没有可识别的详情页（纯单页站），两个数组都输出空数组。
7. 输出 URL 必须逐字来自链接清单的 url 字段，禁止拼接或猜测。

只输出 JSON 对象，不要任何其他文字。'''

LLM4_USR = '''官网：{{#%s.url#}}

自动采样文章页（默认保留）：{{#%s.sampled_articles#}}
自动采样产品页（默认保留）：{{#%s.sampled_products#}}

内部链接清单（JSON 数组，元素为 {"text": 链接文本, "url": 链接地址}）：
{{#%s.internal_links_json#}}

请输出最终采样清单。''' % ("1731000000001", "1731000000005", "1731000000005", "1731000000005")

LLM7_SYS = '''# 评分卡 — GEO 诊断评分与中文撰写

你是 GEO 诊断专家。根据采集数据产出评分对象。**所有面向读者的文字一律用中文撰写**（description / note / findings 各字段 / action / impact_estimate / hint）。枚举机器值（severity / confidence / layer / effort / impact / owner）**保留英文**。

## 输出结构（所有 *_json 字段都是 JSON 字符串，内容是数组或对象）

- description：一段中文核心判断（3-5 句），整体态势与最关键问题（纯文本）
- dimensions_json：{"citability":{"score":0,"note":"..."},"brand":{...},"content":{...},"technical":{...},"schema":{...},"platform":{...}} —— 六维各 0-100 分，note 必填且 ≤120 字，说明得分依据的关键实测值 + 主要扣分点
- findings_json：[{"title":"中文标题","evidence":"中文·引用实测数据","impact":"中文·对大模型引用的影响","fix":"中文·可执行修复","confidence":"Confirmed","severity":"Critical","layer":"domain"}]
- quick_wins_json：[{"action":"中文","impact":"High","effort":"minutes","owner":"developer","platforms":"ChatGPT"}]（本周速赢，≤4 小时可完成）
- medium_term_json：同结构（1-5 天）
- strategic_json：同结构（数周持续投入）
- impact_estimate：中文·保守的量化预估（纯文本）
- paragraph_hints_json：[{"heading":"与段落所属章节标题完全一致","hint":"中文·针对该段原文的具体改写建议"}]，可输出 []；仅当可引用性均分 <50 时为最弱 5 段各写一条，必须引用段落真实短语给替换方案

## 六维评分依据（0-100）

| 维度 | 权重 | 数据来源 | 评分要点 |
|---|---|---|---|
| citability | 25% | 可引用性摘要 | 直接用 average_citability_score；均分低看五维度哪项最弱 |
| brand | 20% | schema(sameAs) + 品牌实体 | Organization 是否在 + sameAs 数量与质量（Wikipedia/Wikidata 权重最高） |
| content | 20% | 可引用性 + 页面摘要 | E-E-A-T：作者署名、来源引用、数据密度、内容深度、新鲜度 |
| technical | 15% | 页面摘要 + 爬虫实测 | SSR 是否完整（has_ssr_content）、安全头、HTTPS、可爬性 |
| schema | 10% | schema 清单 | GEO 关键类型覆盖率、sameAs 完整性（仅对 Organization 强制）、有无 Person/Article |
| platform | 10% | 爬虫实测 | 各 AI 平台爬虫实测可达性（检索类权重最高） |

baseline_scores 是脚本算出的确定性基线，可作参考；你的分作为 override，重点产出 note 与 findings 的中文撰写。

## severity 判定矩阵（硬性约束，三列必须同时满足，任一不符降一级）

| severity | 影响面 | 修复成本 | 建议窗口 |
|---|---|---|---|
| Critical | 阻塞 GEO 基础能力 / 解锁后续多个任务 | ≤ 4 小时 | 0-7 天 |
| High | 对核心 GEO 能力有明显帮助 | ≤ 2 天 | 1-4 周 |
| Medium | 优化项，对 GEO 有一定影响 | ≤ 1 天 | 1-3 月 |
| Low | 边缘优化 / 防御性工作 | 任意 | 长期 |

- 「影响大 + 耗时长」（Wikidata 词条、第三方报道积累）**不能**标 Critical——标 Medium/Low，行动进 strategic
- 「影响小 + 耗时短」（补 favicon、加 referrer-policy）也不能标 Critical

## finding 准入门槛

与 GEO 弱相关的站点安全/性能/合规卫生项（安全响应头、favicon、图片压缩等）不单独成 finding。判断标准：普通 AI 爬虫（GPTBot/ClaudeBot/PerplexityBot）抓取页面时完全不会感知到的问题，不该单独成卡。例外：HTTPS 缺失、钓鱼/仿冒风险、严重混合内容，可单独成卡但 severity 上限 Medium。

## finding 根因归并（防拆分过细）

1. 同根因必须合并，不允许拆成两条不同 severity
2. 现象与测量必须合并（测量数据写进 evidence）
3. 依赖链不拆，但 severity 应保持一致或下游 ≤ 上游，禁止倒挂
4. findings 总数期望 6-10 条；>12 说明拆分过细，<5 说明归并过度

## finding 原子性

修复路径单一动作、一个负责人一个时间窗可完成。fix 若需写「①②③」且耗时差异超 5 倍，应拆开。典型：「sameAs=0」拆为「Organization.sameAs 为空」(Critical/小时级)、「缺第三方平台认领」(High/天级)、「无 Wikidata/Wikipedia 词条」(Medium/月级)。

## 协议权重（GEO 原生 > SEO 原生 > 通用 Web）

llms.txt / JSON-LD Schema / datePublished / sameAs 权重最高；robots.txt / sitemap / canonical / OG 中等；安全响应头/性能/favicon 低。若站点有结构性缺陷（URL 层级混乱、多语言缺 hreflang、纯 CSR 无 SSR），对应 GEO 原生协议价值上调一档，并在 evidence/impact 显式说明放大原因。

## Schema 四态处理

- Present：正常评分；field_issues 逐条转 Medium/High finding
- Broken：evidence 引用 error 与 raw_preview；fix 给可直接落地的代码片段；研判影响边界——若仅个别 script 块损坏而同页其他实体可解析，禁止断言「全站 Schema 崩溃」。含 Organization/WebSite 等核心实体且修复分钟级的，定 Critical/High 作 P0
- Missing：按现状写建设性 finding
- NotSampled：不评分、不写 finding

电商站点专项检查：首页有主推产品推荐区但缺 ItemList 结构化数据 → High/Medium finding（layer: template），fix 给 ItemList JSON-LD 标准代码示例。

## 外链与权威度（防误报）

禁止写「主页未链向任何权威外部平台」，除非 signals 的 citation_count == 0。citation_count > 0 时正确写法是「外链锚文本/上下文不充分」。按站点实际行业自行评估外链权威度（科技：TechRadar/CNET/Wirecutter；咖啡：SCA/Sprudge；工业：ISO/UL；医疗：PubMed/NIH；B2B：G2/Capterra 等）。

## evidence 与 fix 写作规范（客户可读性硬约束）

- 禁写内部文件名（fetch.json / schema.json 等）、内部字段名（total_blocks / has_organization 等）、Python 字面量（[]、{}、False）
- 应写「动作 + 对象 + 观测值」自然语言：抓取了什么页面、看到什么现象。具体数值（HTTP 状态码、词数、百分比、HTTP 头名）保留
- 提及具体页面/外部媒体/验证工具时，名称后紧跟完整 URL：「TechRadar 评测报道（https://www.techradar.com/...）」「Google Rich Results Test（https://search.google.com/test/rich-results）」。不要用 markdown 链接语法

## 页面分析边界（防猜 URL）

- 只诊断真实采集到的页面；功能页以 pages 数据为准，found 且 status_code < 400 才可诊断
- 严禁自行拼 URL（域名+/support 之类）。missing 里的类型写成「未发布 Support 页 / 建议增设 FAQ」建设性 finding
- FAQ：FAQPage schema 已 Present（内嵌也行）→ 「缺独立 FAQ 页」不构成 finding；Missing 且正文有 Q&A → 写「为现有 Q&A 补 FAQPage schema」
- Contact：Organization.contactPoint 已配置或首页/页脚/About 有可见联系方式 → 不构成 finding；全站完全无联系方式才写 Medium/High

## sameAs 评分边界

sameAs 完整性仅对 Organization 强制。对 Person：仅当作者是公开身份的外部专家/KOL 才评分；企业内部编辑/匿名笔名/AI 占位名不评分不写 finding，建议把 Article.author 改为 Organization 或「编辑团队」。

## quick_wins 排序（硬性约束）

数组顺序 = 报告 P0 展示顺序。入选：effort ≤ 4 小时 且 对应 finding severity ∈ {Critical, High}（Medium 仅在不足 3 条时补足）。排序三键降序：finding severity > 解锁作用（Organization schema → Product schema → ItemList、Organization → sameAs 这类前置排前）> 修复成本。禁止把 Medium 排在 Critical/High 前面。

## medium_term 拓扑排序

前置依赖排前（Organization schema → Product/Article schema → sameAs 配置；作者身份确认 → 内容改写）；无依赖按 severity 降序；同级按工作量升序。每条 action 末尾标注「（前置：无）」「（前置：Organization schema 已就位）」等。

## Critical 覆盖自检（输出前必过）

- 每条 Critical finding 必须在 quick_wins 或 medium_term 有对应 action；只能进 strategic 的，在 impact_estimate 开头显式说明原因
- 每个 action 都能在 findings 里找到对应 finding（不无中生有）
- quick_wins 第 1 条是 Critical；medium_term 前置任务在被依赖任务前面

## 未测量与置信度

未测量的项写「未测量」，不猜值。confidence：Confirmed（实测）/ Likely（推断）/ Hypothesis（假设）。

只输出符合 schema 的 JSON 对象，不要任何其他文字。'''

LLM7_USR = '''诊断对象：{{#1731000000001.url#}}（域名 {{#1731000000002.domain#}}，品牌 {{#1731000000001.brand_name#}}）

## 确定性 baseline 评分
{{#1731000000008.baseline_scores#}}

## 规则引擎 issues（带 GEO-XXX-NNN 规则 ID，不要重复诊断，可引用其 ID 与证据）
{{#1731000000008.issues#}}

## 信号摘要
{{#1731000000008.signals_summary#}}

## 品牌站外实体
{{#1731000000008.brand#}}

## 可引用性摘要
{{#1731000000004.citability_summary#}}

## AI 爬虫实测
{{#1731000000004.bots#}}

## Schema 清单（最终版，含补抓页面）
{{#1731000000007.schema#}}

## robots 与 sitemap
{{#1731000000003.robots#}}

## 技术层
{{#1731000000003.technical#}}

## 首页摘要
{{#1731000000003.fetch_summary#}}

## 功能页发现
{{#1731000000005.pages_summary#}}

请按 system prompt 的评分卡输出评分对象。'''

LLM10_SYS = '''# GEO 执行计划撰写指南

你是 GEO 优化执行计划撰写专家。诊断报告回答「问题是什么」，你写的执行计划回答「今天该做哪几件事」——面向运营/开发/内容执行人。全部用中文撰写 Markdown。

## 数据来源（只能从这里取，禁止重新诊断）

- scores 对象里的 findings / quick_wins / medium_term / strategic
- 采集摘要里的实测值（HTTP 状态、词数、URL、爬虫名单、sameAs 缺失项等），用于填充任务的 evidence 与「可直接使用」代码块

## 任务提取规则

- 合并：同一根因引发的多个 finding 合并为一个任务（例：GPTBot 被拦 + ClaudeBot 被拦 + robots 规则错误 → 合并为「修复 robots.txt 的 AI 爬虫访问规则」）
- 拆分：一条建议若含多个可独立完成/验证的工作，拆成多个任务
- 判断标准：每个任务都能独立勾选完成、独立验证

## 任务粒度约束（防碎片化）

- 总数 8-14 项；少于 8 项聚合过度，多于 14 项拆得过碎
- 页面级聚合：同一页面同类改动合并为一个任务，子项列出
- 实体级聚合：同一实体的多个字段（Organization 的 name/logo/sameAs/contactPoint 等）合并为一个任务；Organization + WebSite 通常合并
- 阶段级聚合：品牌外部建设按工作流拆不按平台拆——P1 段（本月）：注册/认领 LinkedIn/YouTube/Crunchbase/Trustpilot 等平台页并写入 sameAs；P3 段（长期）：Wikidata/Wikipedia 词条（需 2 篇以上主流媒体报道）
- 不拆红线：拆分后「涉及页面」「前置依赖」「负责人」完全相同的任务不该拆
- P0 只放阻塞性 + 修复成本低的，理想 3-5 项，超 6 项说明有的该降 P1
- 与 GEO 弱相关的顺手优化（补安全头等）合并进维护任务 checklist，不单独占卡——除非被定为 Critical/High

## 任务字段（每个任务必填）

task_id（TASK-001 递增）/ title（动作+对象，不写虚词）/ priority（P0/P1/P2/P3）/ category（技术基础 | Schema 与实体 | 内容与可引用性 | 品牌外部 | 平台与爬虫）/ estimated_time（具体到分钟/小时，禁用「较短/适中」）/ difficulty（简单/中等/较复杂）/ dependencies（前置 task_id 或「无」）/ why（引用诊断 finding）/ steps（1/2/3 可执行步骤）/ deliverable（产出物）/ verification（完成标准+验证方式）/ status（未开始）

## 优先级判定

- P0：严重技术问题 / 阻碍 AI 爬虫访问 / 其他任务前置 / 修复成本低但影响面大
- P1：品牌实体（Organization schema、sameAs、已有平台认领回链）、Schema 关键类型、核心产品页、内容结构
- P2：内容增强、FAQ、页面细节、引用信号
- P3：低影响优化、长期建设（Wikidata/Wikipedia、Digital PR）、非核心页面

## 排序逻辑

综合 优先级 × 耗时 × 依赖 × 难度，输出真实可执行顺序（例：补 sameAs 依赖 Organization 存在；P0 里 5 分钟的排前面）。

## Markdown 结构骨架（按此顺序）

```markdown
# {品牌} GEO 优化执行清单｜基于 {日期} 诊断报告

> 诊断对象：{url}
> 采样范围：{首页、N 篇文章、M 个产品页、功能页清单}；AI 爬虫实测 N 个 User-Agent
> 使用方式：执行人员完成一项即勾选；每个任务的验收清单全部通过后，再将任务状态改为「已完成」。

> ❗ **真实性红线**：成立时间、公司地址、用户数、产品参数、保修政策、媒体评分、作者身份和社交账号必须有可核验来源。无法确认时应删除相关字段，不得把占位值、推测数据或虚构评价发布到线上。

## 一、诊断基线与优化重点
{一段综合判断 + 六维基线表格：维度 | 基线 | 主要证据 | 阶段目标}
{一段「无需立即改动的部分」豁免说明}

## 二、开始前资料准备清单
{checkbox 列表：公司事实 / 官方账号 / 作者身份 / 联系方式 / 政策 / 量化数据 / 主推 SKU / 评价 / 媒体报道核对}

## 三、优先级与建议排期
{表格：级别 | 任务编号区间 | 报告建议窗口 | 完成情况}

## 四、逐项执行与验收清单
### N. {任务标题}
| 字段 | 值 |
|---|---|
| **优先级** | P0/P1/P2/P3｜严重/高/中/低 |
| **预计工作量** | 具体到分钟/小时 |
| **建议负责人** | 角色组合 |
| **前置依赖** | 前置任务编号或资料确认 |
| **任务状态** | ☐ 未开始　☐ 进行中　☐ 待验收　☐ 已完成 |

**诊断依据。** {引用 finding 的实测数据与影响}
**涉及页面。** {完整可点击 URL 列表}
**执行清单**
- [ ] 每条对应一个可独立验证的动作
**代码块标题**
{可直接复制的 robots.txt / JSON-LD / meta 代码；事实缺失标 > ⚠️ 信息不足，需用户补充：xxx}
**验收清单**
- [ ] 每条对应一个可观测的完成标准
```

## 禁止事项

- 不重复诊断结论（不重新打分、不复制 findings 大段正文）
- 不做收益承诺（禁写「完成后 GEO Score 提升 N 分」）
- 不用 Emoji 作为主要信息结构（真实性红线的 ❗ 除外）；不用 HTML/Mermaid
- 列表缩进 2 空格；执行清单与验收清单统一 `- [ ]` 语法
- URL 字段必须给完整可点击链接
- 涉及 robots.txt / llms.txt / JSON-LD / meta / 页面文案时直接给可复制代码，严禁编造企业名、地址、社交账号 URL 等事实

只输出 plan_markdown 一个字段，内容是完整 Markdown 文档。'''

LLM10_USR = '''诊断对象：{{#1731000000001.url#}}（域名 {{#1731000000002.domain#}}，品牌 {{#1731000000001.brand_name#}}）

## 评分与发现（scores，JSON）
{{#1731000000010.scores_json#}}

## 可引用性摘要（实测值来源）
{{#1731000000004.citability_summary#}}

## AI 爬虫实测（实测值来源）
{{#1731000000004.bots#}}

## Schema 清单（实测值来源）
{{#1731000000007.schema#}}

请基于以上数据撰写 GEO 优化执行清单（Markdown），输出到 plan_markdown。'''

# ── DSL 骨架 ─────────────────────────────────────────────────

def _code_node(nid, title, desc, code, variables, outputs, pos, retry=False):
    node = {
        "data": {
            "selected": False, "title": title, "type": "code",
            "code": code, "code_language": "python3", "desc": desc,
            "variables": variables,
            "outputs": {name: {"children": None, "type": typ}
                        for name, typ in outputs.items()},
        },
        "height": 96, "id": str(nid),
        "position": {"x": pos, "y": 60}, "positionAbsolute": {"x": pos, "y": 60},
        "selected": False, "sourcePosition": "right", "targetPosition": "left",
        "type": "custom", "width": 242,
    }
    if retry:
        node["data"]["retry_config"] = {
            "max_retries": 3, "retry_enabled": True, "retry_interval": 1000}
    return node


def _llm_node(nid, title, desc, sys_prompt, usr_prompt, schema, pos,
              max_tokens=8000, temperature=0.3):
    return {
        "data": {
            "selected": False, "title": title, "type": "llm",
            "desc": desc,
            "context": {"enabled": False, "variable_selector": []},
            "model": {
                "completion_params": {"max_tokens": max_tokens, "temperature": temperature},
                "mode": "chat", "name": "codegeex-4", "provider": "lpf/zhinao/zhinao",
            },
            "prompt_template": [
                {"id": "sys1", "role": "system", "text": sys_prompt},
                {"id": "usr1", "role": "user", "text": usr_prompt},
            ],
            "structured_output": {"schema": schema},
            "structured_output_enabled": True,
            "variables": [], "vision": {"enabled": False},
        },
        "height": 132, "id": str(nid),
        "position": {"x": pos, "y": 60}, "positionAbsolute": {"x": pos, "y": 60},
        "selected": False, "sourcePosition": "right", "targetPosition": "left",
        "type": "custom", "width": 242,
    }


def build_dsl():
    cfg = str(TS + 2)   # 0. 读取配置
    n1, n2, n3 = str(TS + 3), str(TS + 4), str(TS + 5)
    n5, n6 = str(TS + 7), str(TS + 8)
    n8, n9 = str(TS + 10), str(TS + 11)
    n10, n11 = str(TS + 12), str(TS + 13)

    env_vars = [
        {"value_selector": ["env", "biz_api_base"], "variable": "biz_api_base"},
        {"value_selector": ["env", "geo_api_token"], "variable": "geo_api_token"},
    ]

    nodes = [
        {  # start
            "data": {
                "selected": False, "title": "用户输入", "type": "start",
                "variables": [
                    {"default": "", "hint": "", "label": "url", "options": [],
                     "placeholder": "", "required": True, "type": "text-input",
                     "variable": "url"},
                    {"default": "", "hint": "", "label": "brand_name", "options": [],
                     "placeholder": "", "required": False, "type": "text-input",
                     "variable": "brand_name"},
                ],
            },
            "height": 120, "id": str(TS + 1),
            "position": {"x": 80, "y": 280}, "positionAbsolute": {"x": 80, "y": 280},
            "selected": False, "sourcePosition": "right", "targetPosition": "left",
            "type": "custom", "width": 242,
        },
        _code_node(TS + 2, "0. 读取配置",
                   "透传环境变量 + 生成 run_id / 提取 domain / 归一化 url",
                   NODE0_CODE,
                   env_vars + [{"value_selector": [str(TS + 1), "url"], "variable": "url"}],
                   {"biz_api_base": "string", "geo_api_token": "string",
                    "run_id": "string", "domain": "string", "url": "string"},
                   380),
        _code_node(TS + 3, "1. 核心抓取",
                   "diagnosis_fetch_core：首页 fetch + robots + 技术层（带重试）",
                   NODE1_CODE,
                   [{"value_selector": [str(TS + 2), "run_id"], "variable": "run_id"},
                    {"value_selector": [str(TS + 2), "url"], "variable": "url"}] + env_vars,
                   {"ok": "boolean", "error": "string", "fetch_summary": "object",
                    "robots": "object", "technical": "object"},
                   680, retry=True),
        _code_node(TS + 4, "2. 爬虫与引用",
                   "diagnosis_probe_bots：12 个 AI 爬虫 UA 实测 + 段落可引用性（带重试）",
                   NODE2_CODE,
                   [{"value_selector": [str(TS + 2), "run_id"], "variable": "run_id"},
                    {"value_selector": [str(TS + 2), "url"], "variable": "url"}] + env_vars,
                   {"ok": "boolean", "error": "string", "bots": "object",
                    "citability_summary": "object"},
                   980, retry=True),
        _code_node(TS + 5, "3. 页面扫描",
                   "diagnosis_scan_pages：文章/产品抽样 + 功能页发现 + schema（带重试）",
                   NODE3_CODE,
                   [{"value_selector": [str(TS + 2), "run_id"], "variable": "run_id"},
                    {"value_selector": [str(TS + 2), "url"], "variable": "url"}] + env_vars,
                   {"ok": "boolean", "error": "string", "internal_links_json": "string",
                    "sampled_articles": "array[string]",
                    "sampled_products": "array[string]",
                    "pages_summary": "object", "schema": "object"},
                   1280, retry=True),
        _llm_node(TS + 6, "4. LLM 挑页",
                  "从 internal_links 挑文章/产品详情页（对应 Phase 1.5）",
                  LLM4_SYS, LLM4_USR,
                  {"type": "object",
                   "properties": {
                       "articles": {"type": "array", "items": {"type": "string"}},
                       "products": {"type": "array", "items": {"type": "string"}}},
                   "required": ["articles", "products"]},
                  1580, max_tokens=2000, temperature=0.1),
        _code_node(TS + 7, "5. 补抓重建",
                   "diagnosis_rescan_pages：按 LLM 挑选补抓并重建 schema（带重试）",
                   NODE5_CODE,
                   [{"value_selector": [str(TS + 2), "run_id"], "variable": "run_id"},
                    {"value_selector": [str(TS + 6), "articles"], "variable": "articles"},
                    {"value_selector": [str(TS + 6), "products"], "variable": "products"}]
                   + env_vars,
                   {"ok": "boolean", "error": "string", "schema": "object",
                    "sampled_articles": "array[string]",
                    "sampled_products": "array[string]"},
                   1880, retry=True),
        _code_node(TS + 8, "6. 综合分析",
                   "diagnosis_analyze：品牌实体 + signals + 规则引擎 + baseline（带重试）",
                   NODE6_CODE,
                   [{"value_selector": [str(TS + 2), "run_id"], "variable": "run_id"},
                    {"value_selector": [str(TS + 2), "url"], "variable": "url"}] + env_vars,
                   {"ok": "boolean", "error": "string", "brand": "object",
                    "issues": "object", "signals_summary": "object",
                    "baseline_scores": "object"},
                   2180, retry=True),
        _llm_node(TS + 9, "7. LLM 评分",
                  "按评分卡产出六维评分 + 中文 findings + 行动建议（对应 Phase 2）",
                  LLM7_SYS, LLM7_USR,
                  {"type": "object",
                   "properties": {
                       "description": {"type": "string"},
                       "dimensions_json": {"type": "string"},
                       "findings_json": {"type": "string"},
                       "quick_wins_json": {"type": "string"},
                       "medium_term_json": {"type": "string"},
                       "strategic_json": {"type": "string"},
                       "impact_estimate": {"type": "string"},
                       "paragraph_hints_json": {"type": "string"}},
                   "required": ["description", "dimensions_json", "findings_json",
                                "quick_wins_json", "medium_term_json", "strategic_json",
                                "impact_estimate", "paragraph_hints_json"]},
                  2480, max_tokens=8000, temperature=0.3),
        _code_node(TS + 10, "8. 校验修正",
                   "本地解析各 *_json 字段，修正枚举值/缺失维度，输出合并 scores_json",
                   NODE8_CODE,
                   [{"value_selector": [str(TS + 9), "description"], "variable": "description"},
                    {"value_selector": [str(TS + 9), "dimensions_json"], "variable": "dimensions_json"},
                    {"value_selector": [str(TS + 9), "findings_json"], "variable": "findings_json"},
                    {"value_selector": [str(TS + 9), "quick_wins_json"], "variable": "quick_wins_json"},
                    {"value_selector": [str(TS + 9), "medium_term_json"], "variable": "medium_term_json"},
                    {"value_selector": [str(TS + 9), "strategic_json"], "variable": "strategic_json"},
                    {"value_selector": [str(TS + 9), "impact_estimate"], "variable": "impact_estimate"},
                    {"value_selector": [str(TS + 9), "paragraph_hints_json"], "variable": "paragraph_hints_json"},
                    {"value_selector": [str(TS + 8), "baseline_scores"], "variable": "baseline_scores"}],
                   {"ok": "boolean", "error": "string", "scores_json": "string",
                    "fixes_applied": "array[string]"},
                   2780),
        _code_node(TS + 11, "9. 保存评分",
                   "diagnosis_save_scores：把校验后的 scores 写入服务端 run_id 暂存（带重试）",
                   NODE9_CODE,
                   [{"value_selector": [str(TS + 2), "run_id"], "variable": "run_id"},
                    {"value_selector": [str(TS + 10), "scores_json"], "variable": "scores_json"}]
                   + env_vars,
                   {"ok": "boolean", "error": "string"},
                   3080, retry=True),
        _llm_node(TS + 12, "10. LLM 执行计划",
                  "基于 scores 撰写 GEO 优化执行清单 Markdown（对应 Phase 2.5）",
                  LLM10_SYS, LLM10_USR,
                  {"type": "object",
                   "properties": {"plan_markdown": {"type": "string"}},
                   "required": ["plan_markdown"]},
                  3380, max_tokens=8000, temperature=0.4),
        _code_node(TS + 13, "11. 渲染报告",
                   "diagnosis_render：build_report_from_dict + 渲染，直接返回 HTML/MD 字符串",
                   NODE11_CODE,
                   [{"value_selector": [str(TS + 2), "run_id"], "variable": "run_id"},
                    {"value_selector": [str(TS + 2), "url"], "variable": "url"},
                    {"value_selector": [str(TS + 2), "domain"], "variable": "domain"}]
                   + env_vars,
                   {"ok": "boolean", "error": "string", "geo_score": "number",
                    "label_zh": "string", "report_html": "string",
                    "report_markdown": "string", "download_url_html": "string",
                    "download_url_md": "string"},
                   3680, retry=True),
        {  # end
            "data": {
                "selected": False, "title": "输出", "type": "end",
                "outputs": [
                    {"value_selector": [str(TS + 13), "geo_score"], "variable": "geo_score"},
                    {"value_selector": [str(TS + 13), "label_zh"], "variable": "label_zh"},
                    {"value_selector": [str(TS + 13), "report_html"], "variable": "report_html"},
                    {"value_selector": [str(TS + 13), "report_markdown"], "variable": "report_markdown"},
                    {"value_selector": [str(TS + 12), "plan_markdown"], "variable": "execution_plan_md"},
                    {"value_selector": [str(TS + 13), "download_url_html"], "variable": "download_url_html"},
                    {"value_selector": [str(TS + 13), "download_url_md"], "variable": "download_url_md"},
                    {"value_selector": [str(TS + 2), "run_id"], "variable": "run_id"},
                ],
            },
            "height": 270, "id": str(TS + 14),
            "position": {"x": 3980, "y": 21}, "positionAbsolute": {"x": 3980, "y": 21},
            "selected": False, "sourcePosition": "right", "targetPosition": "left",
            "type": "custom", "width": 242,
        },
    ]

    chain = [str(TS + i) for i in range(1, 15)]
    edges = []
    for i in range(len(chain) - 1):
        src, tgt = chain[i], chain[i + 1]
        st = nodes[i]["data"]["type"]
        tt = nodes[i + 1]["data"]["type"]
        edges.append({
            "data": {"isInIteration": False, "isInLoop": False,
                     "sourceType": st, "targetType": tt},
            "id": f"e-{src}-{tgt}", "selected": False,
            "source": src, "sourceHandle": "source",
            "target": tgt, "targetHandle": "target",
            "type": "custom", "zIndex": 0,
        })

    return {
        "app": {
            "description": "中文官网 GEO 诊断：输入 URL，输出 GEO 评分 + HTML 报告 + Markdown 报告 + 执行清单",
            "icon": "🔍", "icon_background": "#FFEAD5",
            "mode": "workflow", "name": "GEO 官网诊断（中文）",
            "use_icon_as_answer_icon": False,
        },
        "dependencies": [
            {"current_identifier": None, "type": "package",
             "value": {"plugin_unique_identifier": "lpf/zhinao:0.0.8@8cebc97adeef5a9c499bf7e8711186c7d377f7fc72d7f497179ce6997491c8c0",
                       "version": None}},
        ],
        "kind": "app", "version": "0.6.0",
        "workflow": {
            "conversation_variables": [],
            "environment_variables": [
                {"description": "GEO 业务工具服务端基础 URL",
                 "id": "b1b2c3d4-0001-4000-8000-000000000001",
                 "name": "biz_api_base", "selector": ["env", "biz_api_base"],
                 "value": "http://123.57.162.4", "value_type": "string"},
                {"description": "GEO API Bearer Token（请在 Dify 环境变量中填写真实值）",
                 "id": "b1b2c3d4-0002-4000-8000-000000000002",
                 "name": "geo_api_token", "selector": ["env", "geo_api_token"],
                 "value": "CHANGE_ME_GEO_ACCESS_TOKEN", "value_type": "string"},
            ],
            "features": {
                "file_upload": {
                    "allowed_file_extensions": [".JPG", ".JPEG", ".PNG", ".GIF", ".WEBP", ".SVG"],
                    "allowed_file_types": ["image"],
                    "allowed_file_upload_methods": ["local_file", "remote_url"],
                    "enabled": False,
                    "fileUploadConfig": {
                        "attachment_image_file_size_limit": 2, "audio_file_size_limit": 50,
                        "batch_count_limit": 5, "file_size_limit": 15,
                        "file_upload_limit": 20, "image_file_batch_limit": 10,
                        "image_file_size_limit": 10, "single_chunk_attachment_limit": 10,
                        "video_file_size_limit": 100, "workflow_file_upload_limit": 10},
                    "image": {"enabled": False, "number_limits": 3,
                              "transfer_methods": ["local_file", "remote_url"]},
                    "number_limits": 3},
                "opening_statement": "",
                "retriever_resource": {"enabled": True},
                "sensitive_word_avoidance": {"enabled": False},
                "speech_to_text": {"enabled": False},
                "suggested_questions": [],
                "suggested_questions_after_answer": {"enabled": False},
                "text_to_speech": {"enabled": False, "language": "", "voice": ""},
            },
            "graph": {
                "edges": edges, "nodes": nodes,
                "viewport": {"x": 0, "y": 0, "zoom": 1},
            },
            "rag_pipeline_variables": [],
        },
    }


if __name__ == "__main__":
    from pathlib import Path
    dsl = build_dsl()
    out = Path(__file__).parent / "workflow.dsl.yaml"
    out.write_text(yaml.safe_dump(dsl, allow_unicode=True, sort_keys=False, width=200),
                   encoding="utf-8")
    print(f"已生成 {out}（{len(dsl['workflow']['graph']['nodes'])} 节点 / "
          f"{len(dsl['workflow']['graph']['edges'])} 边）")
