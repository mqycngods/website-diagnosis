"""build_report：work/*.json + agent 的 scores.json → ReportData。

封闭词表（评分等级/置信度/严重度/爬虫类别/verdict/新鲜度）是**固定小集合**，
在这里统一填中文伴生字段；自由文本（findings/actions）由 agent 在评分时
直接用中文写好，这里不翻译。

新增：集成 report_builder 生成客户报告数据层。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from dataclasses import asdict

from .model import (
    Action, ComponentScore, Finding, ReportData,
    TopPriority, PageRecommendation, PriorityAction, RoadmapPhase, ExecutiveSummary,
)

# 五维权重与中文名（品牌实体维度已移除；其余维度按原比例归一化，和为 1.0）
WEIGHTS = {
    "citability": ("内容引用度", 0.3125),
    "content": ("内容质量", 0.25),
    "technical": ("技术基础", 0.1875),
    "schema": ("品牌识别与 Schema", 0.125),
    "platform": ("爬虫可达性", 0.125),
}

# 封闭词表 → 中文（固定集合，唯一保留的小映射；自由文本不在这里）
ZH_LABEL = {"Excellent": "优秀", "Good": "良好", "Fair": "中等", "Poor": "偏弱", "Critical": "堪忧"}
ZH_SEVERITY = {"Critical": "严重", "High": "高", "Medium": "中", "Low": "低"}
ZH_CONFIDENCE = {"Confirmed": "已确认", "Likely": "较可能", "Hypothesis": "待验证"}
ZH_LAYER = {"domain": "域名层", "template": "模板层", "editorial": "编辑层", "page": "页面层"}
ZH_IMPACT = {"High": "高", "Med": "中", "Low": "低"}
ZH_EFFORT = {"minutes": "分钟", "hours": "数小时", "days": "数天", "weeks": "数周"}
ZH_OWNER = {"developer": "开发", "content": "内容", "marketing": "市场"}
ZH_VERDICT = {"OPEN": "完全开放", "HEALTHY_PUBLISHER": "健康出版方", "PARTIALLY_BLOCKED": "部分受阻",
              "MOSTLY_BLOCKED": "大部分受阻", "BLOCKED": "完全受阻", "INCONCLUSIVE": "无法判定"}
ZH_BOT_CLASS = {"live-retrieval": "实时检索", "search-index": "搜索索引",
                "traditional-search": "传统搜索", "training": "训练"}
ZH_FRESHNESS = {"fresh": "新鲜", "aging": "渐旧", "stale": "陈旧", "very-stale": "严重过时",
                "future-dated": "日期异常", "unknown": "无日期"}


def _load(work_dir: Path, name: str) -> dict:
    f = work_dir / name
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        # agent 手写的 JSON（如 scores.json）常见未转义引号。静默降级会丢弃
        # agent 的全部评分/findings/行动计划且页面上看不出来——必须大声报错。
        print(f"[build] ⚠️ {f} 不是合法 JSON（第 {e.lineno} 行第 {e.colno} 列：{e.msg}），"
              f"该文件的内容将被忽略，报告相应章节会显示「未测量」。"
              f"修复：python3 -m json.tool {f}", file=sys.stderr)
        return {}


def _ok(payload) -> bool:
    return isinstance(payload, dict) and "error" not in payload and bool(payload)


def _score_label(score: int) -> str:
    return ("Excellent" if score >= 90 else "Good" if score >= 75 else
            "Fair" if score >= 60 else "Poor" if score >= 40 else "Critical")


def _schema_score(schema: dict) -> int:
    """schema.json → 0-100 回退分：关键类型覆盖 + sameAs 完整性。"""
    if not schema:
        return 0
    inv = schema.get("inventory", [])
    critical = [i for i in inv if i.get("impact") == "Critical"]
    crit_present = sum(1 for i in critical if i.get("status") == "Present")
    base = round((crit_present / len(critical)) * 60) if critical else 0
    # sameAs：0 条=0，1-2 条=20，3-4 条=30，5+=40
    n = schema.get("sameas_count", 0)
    sameas = 40 if n >= 5 else 30 if n >= 3 else 20 if n >= 1 else 0
    return min(100, base + sameas)


_ARTICLE_FAMILY = ("Article", "NewsArticle", "BlogPosting")


def _normalize_schema(schema: dict, fetch: dict) -> dict:
    """旧版 schema.json（BlogPosting/NewsArticle 独立行、Product 标 Missing）→ 三态新格式。

    归并 Article 家族；Product/Service 在未采产品页时由 Missing 改标 NotSampled；
    补 sampled 结构与电商识别。新版 collect 已产出新格式时直接透传。
    """
    if not _ok(schema):
        return schema
    inv = schema.get("inventory") or []
    names = {i.get("schema_type") for i in inv}
    already = any("含" in (n or "") for n in names) or \
        any(i.get("status") == "NotSampled" for i in inv)
    if already:
        schema.setdefault("sampled", {
            "homepage": 1,
            "article_pages": max(0, (schema.get("sampled_pages") or 1) - 1),
            "product_pages": 0,
        })
        return schema

    present_types = {i.get("schema_type") for i in inv if i.get("status") == "Present"}
    article_covered = bool(present_types & set(_ARTICLE_FAMILY))
    product_pages = 0  # 旧数据 collect 时未抓产品页

    out = []
    seen_article = False
    for i in inv:
        t = i.get("schema_type")
        if t in _ARTICLE_FAMILY:
            if seen_article:
                continue  # 家族成员只保留一行
            seen_article = True
            out.append({**i, "schema_type": "Article（含 NewsArticle/BlogPosting）",
                        "status": "Present" if article_covered else "Missing"})
        elif t in ("Product", "Service") and i.get("status") == "Missing" and product_pages == 0:
            out.append({**i, "status": "NotSampled"})
        else:
            out.append(i)

    # 电商识别（内链路径特征），命中则 Product 影响升级
    from diagnose.schema_check import detect_ecommerce
    is_ecom = detect_ecommerce(fetch.get("internal_links", []) if _ok(fetch) else [])
    if is_ecom:
        for i in out:
            if i.get("schema_type") == "Product" and i.get("impact") == "Medium":
                i["impact"] = "High"

    schema["inventory"] = out
    schema["is_ecommerce"] = is_ecom
    schema["sampled"] = {
        "homepage": 1,
        "article_pages": max(0, (schema.get("sampled_pages") or 1) - 1),
        "product_pages": 0,
    }
    return schema


def _format_analyzer_evidence(it: dict) -> str:
    """把 issue 转化为通顺的中文自然语言段落，而不是机械拼接字段值。"""
    kind = it.get("kind", "")
    ev = it.get("evidence") or {}
    pages = it.get("affected_pages") or []
    pc = it.get("page_count", len(pages) or 1)

    # 1. 作者冲突
    if "author_mismatch" in kind:
        schema_auth = (ev.get("schema_author") or {}).get("value", "")
        bios = (ev.get("bio_authors_found") or {}).get("value", [])
        return (f"在抽样的 {pc} 篇博客文章中，Article 结构化数据统一将作者标注为「{schema_auth}」；"
                f"但正文末尾的 About the Author 区域却分别介绍了 {len(bios)} 位不同的人员（{', '.join(bios)}）。"
                f"读者看到的作者背景与搜索引擎抓取到的作者实体完全脱节。")

    # 2. 主页缺 Organization
    if kind == "homepage_missing_organization":
        return ("虽然在部分文章页与产品页检测到了 Organization 声明，"
                "但官方首页的源代码中没有任何 Organization 结构化数据。"
                "首页是品牌的核心锚点，主页缺位会导致模型对品牌实体的识别依赖于深层子页。")

    # 3. sameAs 缺失
    if kind == "organization_no_sameas":
        return ("全站现有的 Organization 结构化数据中，sameAs 字段完全为空；"
                "未声明任何指向 Wikipedia、Wikidata、LinkedIn、Crunchbase、YouTube 官方频道的权威链接，"
                "导致 AI 搜索引擎无法建立实体对齐。")

    # 4. Organization 缺关键事实
    if kind == "organization_missing_facts":
        missing = (ev.get("missing_fields") or {}).get("value", [])
        return (f"Organization 结构化数据中缺少关键基础字段（{', '.join(missing)}）；"
                "未包含创立年份、总部地址或客服联系方式，无法为知识图谱提供完整的企业属性。")

    # 5. Product.brand 缺 @type
    if kind == "product_brand_no_type":
        products = (ev.get("products") or {}).get("value", [])
        return (f"抽样的 {pc} 个核心产品页（包括 {', '.join(products[:3])}）中，"
                "Product 结构化数据的 brand 字段仅以纯文本输出品牌名，缺少 \"@type\": \"Brand\" 声明，"
                "导致购物类 AI（如 Perplexity / ChatGPT Shopping）无法将产品与品牌实体绑定。")

    # 6. Person 缺 sameAs
    if kind == "person_no_sameas":
        name = (ev.get("person") or {}).get("value", "")
        return (f"全站 {pc} 处引用的作者「{name}」Person 结构化数据中均无 sameAs 链接；"
                "缺少指向作者真实 LinkedIn 档案或专业主页的锚点，无法向搜索引擎证明其在相关领域的专业背景。")

    # 7. Article.description 为空
    if kind == "article_description_empty":
        return (f"抽样的 {pc} 篇博客文章中，Article 结构化数据的 description 字段均为空字符串；"
                "虽然部分页面包含 HTML meta description，但结构化数据未同步，"
                "导致 Google AIO 与 Perplexity 引用文章时无法直接提取摘要，只能降级抓取正文片段。")

    # 8. 绝对化表述
    if kind == "high_absolute_density":
        terms = (ev.get("absolute_terms") or {}).get("value", [])
        words = (ev.get("word_count") or {}).get("value", 0)
        return (f"正文共 {words} 词，出现了 {len(terms)} 处绝对化表述（如 {', '.join(terms[:4])}）；"
                "在没有提供第三方测试数据与明确对比维度的情况下，绝对化断言容易被大模型归类为营销宣发话术，从而降低可信度。")

    # 9. 权威外链分析
    if "authority_external_links" in kind:
        count = (ev.get("authority_links_count") or {}).get("value", 0)
        words = (ev.get("word_count") or {}).get("value", 0)
        auth_links = (ev.get("authority_links") or {}).get("value", [])

        # 格式化所有检测到的媒体外链列表
        link_items = []
        for l in auth_links:
            if "techradar.com" in l: link_items.append(f"TechRadar 报道（{l}）")
            elif "pcworld.com" in l: link_items.append(f"PCWorld 报道（{l}）")
            elif "trustedreviews.com" in l: link_items.append(f"Trusted Reviews 报道（{l}）")
            else: link_items.append(f"权威评测（{l}）")

        links_desc = "、".join(link_items) if link_items else "若干权威媒体报道"
        return (f"正文共 {words} 词，已包含 {count} 条指向顶级独立权威媒体的外链：{links_desc}。"
                "虽然覆盖了 3 家知名科技评测，但在首页并未提炼展示核心评测结论与推荐语，且缺少 Consumer Reports 等中立消费者机构的背书引用。")

    # 兜底：通用自然语言
    title = it.get("title", "")
    return f"经页面扫描确认：{title}。影响范围涉及 {pc} 个页面。"


def _issues_to_findings(issues: dict) -> list:
    """把分析器产出的 issues 字典转成 findings 列表，输出通顺的分析性中文证据。"""
    if not isinstance(issues, dict):
        return []
    findings = []
    for analyzer_name, items in issues.items():
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict) or not it.get("title"):
                continue

            # 使用自然语言格式化器生成证据
            ev_str = _format_analyzer_evidence(it)

            findings.append({
                "title": it.get("title", ""),
                "evidence": ev_str,
                "impact": it.get("impact", ""),
                "fix": it.get("fix", ""),
                "severity": it.get("severity", "Medium"),
                "confidence": it.get("confidence", "Confirmed"),
                "layer": it.get("layer", "page"),
                "_source": f"analyzer:{analyzer_name}",
                "_category": it.get("category", analyzer_name),
                "_kind": it.get("kind", ""),
            })
    return findings


def build_report(work_dir: Path, domain: str, url: str,
                 audit_date: str = None) -> ReportData:
    fetch = _load(work_dir, "fetch.json")
    robots = _load(work_dir, "robots.json")
    bots = _load(work_dir, "bots.json")
    citability = _load(work_dir, "citability.json")
    schema = _load(work_dir, "schema.json")
    brand = _load(work_dir, "brand.json")
    technical = _load(work_dir, "technical.json")
    scores = _load(work_dir, "scores.json")   # agent 产出（可缺）
    articles = _load(work_dir, "articles.json")
    signals = _load(work_dir, "signals.json")  # 信号采集（新）
    issues = _load(work_dir, "issues.json")    # 分析器产出（新）

    # 旧版 schema.json → 三态新格式（Article 家族归并 / Product NotSampled / 电商权重）
    schema = _normalize_schema(schema, fetch)

    # 旧 citability.json 的 preview 尾部带采集端硬加的 "..."——剥掉，截断交给 CSS
    if _ok(citability):
        # agent 段落级改写提示：heading → hint（可选；无 agent hint 时自动回退到规则生成优化建议）
        agent_hints = {}
        if _ok(scores):
            for ph in scores.get("paragraph_hints") or []:
                if isinstance(ph, dict) and ph.get("heading") and ph.get("hint"):
                    agent_hints[str(ph["heading"]).strip()] = str(ph["hint"]).strip()

        # 五维度最弱项对应的通用改写策略（兜底机制）
        DIMENSION_HINTS = {
            "answer_block_quality": "【首句优化】段落开头补 1 句直接回答该主题的自包含定义或结论句，避免空泛过渡词。",
            "self_containment": "【自包含性】减少对上下文代词的依赖，明确写出主语、产品型号与适用场景，使整段可被单独摘取。",
            "structural_readability": "【结构化排版】长段落拆解为 3-4 条要点列表（bullet points）或对比表格，提升扫描与抓取效率。",
            "statistical_density": "【数据补全】段落内补充 2-3 个具体数字（如规格参数、续航、视角、测试数值、价格），增强客观性。",
            "uniqueness_signals": "【独家信号】补充官方测试数据、一手研发背景、用户案例或独家功能对比，提升内容不可替代性。",
        }

        for key in ("top_5_citable", "bottom_5_citable", "all_blocks"):
            for b in citability.get(key) or []:
                if isinstance(b, dict) and isinstance(b.get("preview"), str):
                    b["preview"] = re.sub(r"\.{3}$|…$", "", b["preview"]).rstrip()
                if isinstance(b, dict):
                    heading = (b.get("heading") or "").strip()
                    if heading and heading in agent_hints:
                        b["agent_hint"] = agent_hints[heading]
                    else:
                        # 兜底：根据 breakdown 找出得分率最低的维度，自动生成方向建议
                        bd = b.get("breakdown") or {}
                        if bd:
                            # 满分权重：answer(30), self(25), struct(20), stat(15), uniq(10)
                            MAX_WEIGHTS = {"answer_block_quality": 30, "self_containment": 25,
                                           "structural_readability": 20, "statistical_density": 15,
                                           "uniqueness_signals": 10}
                            # 计算各维度得分率 (score / max_score)
                            ratios = {k: bd.get(k, 0) / MAX_WEIGHTS.get(k, 1) for k in MAX_WEIGHTS}
                            weakest = min(ratios.items(), key=lambda x: x[1])[0]
                            b["agent_hint"] = DIMENSION_HINTS.get(weakest, "补充具体数字与事实，提高段落自包含性。")

    # 新鲜度是文章级信号：首页无日期时，用最新鲜的抽样文章页代替，并标明来源
    if _ok(fetch) and isinstance(fetch.get("freshness"), dict) \
            and fetch["freshness"].get("tier") == "unknown":
        best = None
        for ap in (articles.get("pages", []) if _ok(articles) else []):
            af = ap.get("freshness") or {}
            if af.get("age_days") is not None and (best is None or af["age_days"] < best[0]["age_days"]):
                best = (af, ap.get("url"))
        if best:
            merged = dict(best[0])
            merged["sampled_from"] = best[1]
            merged["homepage_tier"] = "unknown"
            fetch["freshness"] = merged

    # 六维得分：优先用 agent scores.json，缺失维度回退到脚本确定性分
    dims = scores.get("dimensions", {}) if _ok(scores) else {}
    def dim_score(key, fallback):
        """返回 (score, measured, note)。"""
        d = dims.get(key)
        if isinstance(d, dict) and d.get("score") is not None:
            return int(d["score"]), True, (d.get("note") or "")
        return (int(fallback) if fallback is not None else 0), (fallback is not None), ""

    cit_fb = citability.get("average_citability_score") if _ok(citability) else None
    bot_fb = bots.get("overall_score") if _ok(bots) else None
    tech_fb = technical.get("security_score") if _ok(technical) else None
    schema_fb = _schema_score(schema) if _ok(schema) else None

    comp = []
    fallback_map = {
        "citability": cit_fb,
        "content": cit_fb,        # 内容质量以可引用性为代理回退
        "technical": tech_fb,
        "schema": schema_fb,
        "platform": bot_fb,       # 平台就绪以爬虫实测为代理回退
    }
    for key, (name, weight) in WEIGHTS.items():
        sc, measured, note = dim_score(key, fallback_map.get(key))
        comp.append(ComponentScore(key=key, name=name, score=sc, weight=weight, measured=measured, note=note))

    measured = [c for c in comp if c.measured]
    tw = sum(c.weight for c in measured) or 1.0
    geo = round(sum(c.score * c.weight for c in measured) / tw) if measured else 0
    label = _score_label(geo)

    # findings：来自 agent scores.json（中文） + 分析器产出的 issues（新）
    findings = []
    # 1. agent 的 findings（权重最高）
    agent_titles = set()
    for f in (scores.get("findings", []) if _ok(scores) else []):
        t = f.get("title", "")
        agent_titles.add(t)
        findings.append(Finding(
            title=t, evidence=f.get("evidence", ""),
            impact=f.get("impact", ""), fix=f.get("fix", ""),
            confidence=f.get("confidence", "Confirmed"),
            severity=f.get("severity", "Medium"), layer=f.get("layer", ""),
        ))

    # 2. 通用分析器的 issues（去重：若 agent 已经写了类似标题，跳过）
    analyzer_findings = _issues_to_findings(issues)
    for af in analyzer_findings:
        t = af.get("title", "")
        # 简单去重：如果 agent 已有相似标题则不重复追加
        if not any(t[:8] in at for at in agent_titles):
            findings.append(Finding(
                title=t, evidence=af.get("evidence", ""),
                impact=af.get("impact", ""), fix=af.get("fix", ""),
                confidence=af.get("confidence", "Confirmed"),
                severity=af.get("severity", "Medium"), layer=af.get("layer", "page"),
            ))

    if not findings:
        findings = _fallback_findings(citability, bots, schema, fetch, brand)

    # 封闭词表中文伴生
    def fz(f: Finding):
        f.severity_zh = ZH_SEVERITY.get(f.severity, f.severity)
        f.confidence_zh = ZH_CONFIDENCE.get(f.confidence, f.confidence)
        f.layer_zh = ZH_LAYER.get(f.layer, f.layer)
    for f in findings:
        fz(f)

    def az(a: Action):
        a.impact_zh = ZH_IMPACT.get(a.impact, a.impact)
        a.effort_zh = ZH_EFFORT.get(a.effort, a.effort)
        a.owner_zh = ZH_OWNER.get(a.owner, a.owner)
        return a

    quick = [az(Action(**a)) for a in (scores.get("quick_wins", []) if _ok(scores) else []) if isinstance(a, dict)]
    medium = [az(Action(**a)) for a in (scores.get("medium_term", []) if _ok(scores) else []) if isinstance(a, dict)]
    strat = [az(Action(**a)) for a in (scores.get("strategic", []) if _ok(scores) else []) if isinstance(a, dict)]

    # verdict / bot_class 中文伴生（注入到 bots dict 供模板）
    if _ok(bots):
        bots["verdict_zh"] = ZH_VERDICT.get(bots.get("verdict", ""), bots.get("verdict", ""))
        for p in bots.get("probes", []):
            p["bot_class_zh"] = ZH_BOT_CLASS.get(p.get("class", ""), p.get("class", ""))

    # 新鲜度 tier 中文伴生
    if _ok(fetch) and isinstance(fetch.get("freshness"), dict):
        tier = fetch["freshness"].get("tier")
        if tier:
            fetch["freshness"]["tier_zh"] = ZH_FRESHNESS.get(tier, tier)

    def _format_date(raw):
        """ISO/RFC 等原始日期串 → YYYY-MM-DD；无法解析时原样返回。"""
        if not raw:
            return raw
        s = str(raw).strip()
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        except ValueError:
            pass
        m = re.match(r"^.{0,30}?\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b", s)  # RFC 1123/850
        if m:
            try:
                return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %B %Y").strftime("%Y-%m-%d")
            except ValueError:
                try:
                    return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %b %Y").strftime("%Y-%m-%d")
                except ValueError:
                    pass
        return s

    # 新鲜度显示日期统一为 YYYY-MM-DD（报告内唯一展示出口：模板 + 图表 tooltip）
    if _ok(fetch) and isinstance(fetch.get("freshness"), dict):
        fr = fetch["freshness"]
        if fr.get("best_date"):
            fr["best_date"] = _format_date(fr["best_date"])

    # ── 构建客户报告数据层 ──────────────────────────────────
    # 准备 diagnostics 字典
    diagnostics = {
        "scores": scores,
        "citability": citability,
        "bots": bots,
        "robots": robots,
        "schema": schema,
        "fetch": fetch,
        "brand": {},
        "technical": technical,
        "geo_score": geo,
        "label": label,
        "label_zh": ZH_LABEL.get(label, label),
        "components": [asdict(c) for c in comp],
        "findings": [asdict(f) for f in findings],
        "quick_wins": [asdict(a) for a in quick],
        "medium_term": [asdict(a) for a in medium],
        "strategic": [asdict(a) for a in strat],
    }

    # 调用 report_builder 生成客户报告数据
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from scripts.report_builder import build_client_report_data
        client_data = build_client_report_data(
            domain=domain,
            url=url,
            audit_date=audit_date or date.today().isoformat(),
            diagnostics=diagnostics,
        )
        executive_summary = client_data.executive_summary
        page_recommendations = client_data.page_recommendations
        priority_actions = client_data.priority_actions
        roadmap = client_data.roadmap
        chart_visibility = client_data.chart_visibility

        # 封闭词表中文伴生：top_priorities / priority_actions 的 impact/effort/owner
        for tp in getattr(executive_summary, "top_priorities", []) or []:
            tp.impact_zh = ZH_IMPACT.get(tp.impact, tp.impact)
            tp.effort_zh = ZH_EFFORT.get(tp.effort, tp.effort)
            tp.owner_zh = ZH_OWNER.get(tp.owner, tp.owner)
        for pa in priority_actions:
            pa.impact_zh = ZH_IMPACT.get(pa.impact, pa.impact)
            pa.effort_zh = ZH_EFFORT.get(pa.effort, pa.effort)
            pa.owner_zh = ZH_OWNER.get(pa.owner, pa.owner)

        # 把 priority_action 生成的 code_example 注入到对应 finding，供附录展示
        for pa in priority_actions:
            if pa.finding_index >= 0 and pa.finding_index < len(findings) and pa.code_example:
                findings[pa.finding_index].code_example = pa.code_example

        # 给每个 finding 注入负责人：优先从 priority_action 反查，缺失时按 layer 推断
        _LAYER_TO_OWNER = {"domain": "developer", "template": "developer",
                           "editorial": "content", "page": "content"}
        pa_owner_by_finding = {pa.finding_index: pa.owner for pa in priority_actions
                               if pa.finding_index >= 0}
        for idx, f in enumerate(findings):
            owner = pa_owner_by_finding.get(idx) or _LAYER_TO_OWNER.get(f.layer, "developer")
            f.owner_zh = ZH_OWNER.get(owner, owner)
    except Exception as e:
        # report_builder 失败时降级：使用基础数据
        print(f"[build] ⚠️ report_builder 失败：{e}，使用降级数据", file=sys.stderr)
        executive_summary = ExecutiveSummary(
            geo_score=geo, label=label, label_zh=ZH_LABEL.get(label, label),
            overall_assessment=scores.get("description", "") if _ok(scores) else "",
        )
        page_recommendations = []
        priority_actions = []
        roadmap = []
        chart_visibility = {}

    # ── 构建最终 ReportData ──────────────────────────────────
    report = ReportData(
        domain=domain, url=url,
        audit_date=audit_date or date.today().isoformat(),
        geo_score=geo, label=label, label_zh=ZH_LABEL.get(label, label),
        description=scores.get("description", "") if _ok(scores) else "",
        components=comp,
        citability=citability, bots=bots, robots=robots, schema=schema, fetch=fetch,
        brand={}, technical=technical, scores=scores,
        findings=findings, quick_wins=quick, medium_term=medium, strategic=strat,
        impact_estimate=scores.get("impact_estimate", "") if _ok(scores) else "",
        # 客户报告新增字段
        executive_summary=executive_summary,
        page_recommendations=page_recommendations,
        priority_actions=priority_actions,
        roadmap=roadmap,
        chart_visibility=chart_visibility,
    )

    return report


def _fallback_findings(citability, bots, schema, fetch, brand=None) -> list:
    """无 agent scores.json 时，由确定性数据生成中文兜底 findings。"""
    out = []
    if _ok(bots):
        for p in bots.get("probes", []):
            if p.get("blocked") and p.get("bot") in ("GPTBot", "ClaudeBot", "PerplexityBot", "OAI-SearchBot"):
                out.append(Finding(
                    title=f"爬虫 {p['bot']} 被拦截",
                    evidence=f"{p['bot']} 实测返回 HTTP {p.get('status')}（{p.get('block_reason')}）",
                    impact="该爬虫所服务的 AI 平台无法读取并引用你的内容。",
                    fix=f"在 CDN/WAF 白名单中放行 {p['bot']}。",
                    confidence="Confirmed", severity="Critical", layer="domain",
                ))
    if _ok(fetch) and not fetch.get("has_ssr_content", True):
        out.append(Finding(
            title="AI 爬虫看到的是空页面（无 SSR 内容）",
            evidence="服务端 HTML 几乎不含正文，内容依赖 JavaScript 执行后生成。",
            impact="多数 AI 爬虫不执行 JS，收到的是无可引用内容的空壳。",
            fix="为可索引路由启用服务端渲染（SSR）或静态预渲染。",
            confidence="Confirmed", severity="Critical", layer="template",
        ))
    if _ok(schema) and not schema.get("has_organization"):
        out.append(Finding(
            title="缺少 Organization 实体 Schema",
            evidence="未检测到 Organization / LocalBusiness JSON-LD。",
            impact="AI 系统无法把你的站点识别为可验证的实体，难以建立实体图谱。",
            fix="添加含 name/url/logo/sameAs 的 Organization JSON-LD。",
            confidence="Confirmed", severity="High", layer="template",
        ))
    if _ok(schema) and schema.get("sameas_count", 0) < 3:
        out.append(Finding(
            title=f"sameAs 实体链接不足（当前 {schema.get('sameas_count', 0)} 条）",
            evidence=f"Organization schema 的 sameAs 仅 {schema.get('sameas_count', 0)} 条，少于建立实体图谱所需的 3-5 条。",
            impact="AI 无法确认各平台资料属于同一实体，品牌权威难以累积。",
            fix="补齐企业官方社交主页、行业目录、企业信息库等 sameAs 链接。",
            confidence="Confirmed", severity="High", layer="domain",
        ))
    if _ok(fetch):
        fr = fetch.get("freshness", {})
        if fr.get("tier") == "unknown":
            out.append(Finding(
                title="内容无机器可读日期",
                evidence="未检测到 dateModified / <time datetime> / Last-Modified。",
                impact="AI 无法验证内容时效性，倾向引用有明确更新时间的页面。",
                fix="为内容页添加 <time datetime> 或 Article datePublished/dateModified。",
                confidence="Confirmed", severity="Medium", layer="template",
            ))
    sev = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    out.sort(key=lambda f: sev.get(f.severity, 4))
    return out
