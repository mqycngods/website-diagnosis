"""RecommendationEngine — 从诊断数据派生客户报告数据对象与行动建议。

职责（Finding → Recommendation → Roadmap）：
- 数据聚合与分类
- 优先级排序（P0/P1/P2/P3）
- 页面级建议生成
- Executive Summary 生成
- Roadmap 构建（30/60/90 天，通过 finding_index 引用不复制正文）
- 图表选择决策

不修改原始诊断数据，只读取并转换。Finding（问题是什么）与
Recommendation（具体做什么）分离：findings 由诊断层/agent 产出，
本模块只做派生与编排，不重新诊断。

（实现自 scripts/report_builder.py 迁入；原文件保留为兼容薄壳。）
"""
from __future__ import annotations

from dataclasses import dataclass, field

from diagnose.models import (
    SEVERITY_TO_PRIORITY,
    TopPriority, PageRecommendation, PriorityAction, RoadmapPhase, ExecutiveSummary,
)


# ── 优先级定义 ──────────────────────────────────────────────
# P0: 阻碍 GEO 基础能力（AI 爬虫无法访问、核心页面无法抓取）
# P1: 对 GEO 有明显影响（品牌定义不清、核心产品信息不足、Citability 不足）
# P2: 优化项（Schema Enhancement、FAQ、内容结构优化）
# P3: 长期建设（Digital PR、第三方权威、Knowledge Graph）

PRIORITY_THRESHOLDS = {
    "P0": {"max": 0, "label": "紧急", "color": "red", "description": "阻碍 GEO 基础能力，需立即修复"},
    "P1": {"max": 1, "label": "高优", "color": "orange", "description": "对 GEO 有明显影响，应优先解决"},
    "P2": {"max": 2, "label": "优化", "color": "amber", "description": "优化项，提升 GEO 表现"},
    "P3": {"max": 3, "label": "长期", "color": "green", "description": "长期建设，持续投入"},
}

# SEVERITY_TO_PRIORITY 已移至 diagnose.models（单一事实来源），上方 import。

# ── 数据结构 ──────────────────────────────────────────────
# TopPriority / PageRecommendation / PriorityAction / RoadmapPhase /
# ExecutiveSummary 的唯一定义在 diagnose/models.py（上方 import）。


@dataclass
class ClientReportData:
    """客户报告数据对象 — 报告层的最终输出"""
    # 执行摘要
    executive_summary: ExecutiveSummary = field(default_factory=lambda: ExecutiveSummary(0, "", ""))

    # 原始诊断数据（透传）
    domain: str = ""
    url: str = ""
    audit_date: str = ""
    geo_score: int = 0
    label: str = ""
    label_zh: str = ""
    components: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    quick_wins: list = field(default_factory=list)
    medium_term: list = field(default_factory=list)
    strategic: list = field(default_factory=list)

    # 原始 JSON 数据（透传）
    citability: dict = field(default_factory=dict)
    bots: dict = field(default_factory=dict)
    robots: dict = field(default_factory=dict)
    schema: dict = field(default_factory=dict)
    fetch: dict = field(default_factory=dict)
    brand: dict = field(default_factory=dict)
    technical: dict = field(default_factory=dict)
    scores: dict = field(default_factory=dict)

    # 报告层生成数据
    page_recommendations: list = field(default_factory=list)   # list[PageRecommendation]
    priority_actions: list = field(default_factory=list)       # list[PriorityAction]
    roadmap: list = field(default_factory=list)                # list[RoadmapPhase]

    # 图表选择决策
    chart_visibility: dict = field(default_factory=dict)

    # 影响预估
    impact_estimate: str = ""


# ── 构建函数 ──────────────────────────────────────────────

def build_client_report_data(
    domain: str,
    url: str,
    audit_date: str,
    diagnostics: dict,
) -> ClientReportData:
    """
    将原始 GEO 诊断数据转换为 Client Report Data Object。

    Args:
        domain: 域名
        url: 完整 URL
        audit_date: 审计日期
        diagnostics: 原始诊断数据字典，包含：
            - scores: agent 评分产出
            - citability: 可引用性数据
            - bots: 爬虫实测数据
            - robots: robots.txt 数据
            - schema: Schema 数据
            - fetch: 页面抓取数据
            - brand: 品牌实体数据
            - technical: 技术层数据

    Returns:
        ClientReportData 对象
    """
    scores = diagnostics.get("scores", {})
    citability = diagnostics.get("citability", {})
    bots = diagnostics.get("bots", {})
    robots = diagnostics.get("robots", {})
    schema = diagnostics.get("schema", {})
    fetch = diagnostics.get("fetch", {})
    brand = diagnostics.get("brand", {})
    technical = diagnostics.get("technical", {})

    # 基础分数
    geo_score = diagnostics.get("geo_score", 0)
    label = diagnostics.get("label", "")
    label_zh = diagnostics.get("label_zh", "")
    components = diagnostics.get("components", [])
    findings = diagnostics.get("findings", [])
    quick_wins = diagnostics.get("quick_wins", [])
    medium_term = diagnostics.get("medium_term", [])
    strategic = diagnostics.get("strategic", [])

    # 构建报告层数据
    report = ClientReportData(
        domain=domain,
        url=url,
        audit_date=audit_date,
        geo_score=geo_score,
        label=label,
        label_zh=label_zh,
        components=components,
        findings=findings,
        quick_wins=quick_wins,
        medium_term=medium_term,
        strategic=strategic,
        citability=citability,
        bots=bots,
        robots=robots,
        schema=schema,
        fetch=fetch,
        brand=brand,
        technical=technical,
        scores=scores,
        impact_estimate=scores.get("impact_estimate", ""),
    )

    # 构建 Executive Summary
    report.executive_summary = _build_executive_summary(
        geo_score, label, label_zh, findings, scores, quick_wins,
        p0_blockers=diagnostics.get("p0_blockers"),
    )

    # 构建页面级建议
    report.page_recommendations = _build_page_recommendations(
        findings, schema, fetch, citability, brand, url=url
    )

    # 构建优先级行动
    report.priority_actions = _build_priority_actions(
        findings, quick_wins, medium_term, strategic, fetch=fetch, url=url
    )

    # 构建 Roadmap（引用 priority_actions 的 finding_index，不再复制正文）
    report.roadmap = _build_roadmap(
        quick_wins, medium_term, strategic, findings, report.priority_actions
    )

    # 图表选择决策
    report.chart_visibility = _decide_chart_visibility(
        components, citability, bots, schema, brand, fetch, technical
    )

    return report


def _build_executive_summary(
    geo_score: int,
    label: str,
    label_zh: str,
    findings: list,
    scores: dict,
    quick_wins: list,
    p0_blockers: "list | None" = None,
) -> ExecutiveSummary:
    """构建执行摘要"""
    # 从 scores.json 获取整体评估
    description = scores.get("description", "")

    # 提取 Top 3 Priorities（基于 findings 的严重度排序）
    sorted_findings = sorted(
        findings,
        key=lambda f: {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}.get(f.get("severity", "Medium"), 4)
    )

    top_priorities = []
    for i, f in enumerate(sorted_findings[:3]):
        severity = f.get("severity", "Medium")
        priority = TopPriority(
            index=i + 1,
            title=f.get("title", ""),
            description=f.get("impact", ""),
            # chip 只显示影响级别（高/中/低），长描述已在 description 中
            impact=_severity_to_impact(severity),
            effort=_infer_effort(f),
            owner=_infer_owner(f),
        )
        top_priorities.append(priority)

    # 推荐下一步
    recommended_next_step = _derive_next_step(findings, quick_wins)

    # P0 前置修改判定（规则同源）：优先读 analyze 阶段确定性算出的 p0_blockers
    # （diagnose/blockers.py），报告横幅与执行清单前置判定同源一致；
    # p0_blockers 为 None（旧数据/未跑 analyze）时回退到 Critical finding 计数。
    from diagnose.blockers import p0_blocking_note as _p0_note
    if p0_blockers is not None:
        note = _p0_note(p0_blockers if isinstance(p0_blockers, list) else [])
    else:
        p0_count = sum(1 for f in findings if f.get("severity") == "Critical")
        if p0_count > 0:
            note = (f"当前官网不可直接 GEO 优化，包含 {p0_count} 项 P0 级任务需修改后进行；"
                    "在这些问题修复之前，其余优化动作难以生效。")
        else:
            note = _p0_note([])

    return ExecutiveSummary(
        geo_score=geo_score,
        label=label,
        label_zh=label_zh,
        overall_assessment=description,
        top_priorities=top_priorities,
        recommended_next_step=recommended_next_step,
        p0_blocking_note=note,
    )


def _build_page_recommendations(
    findings: list,
    schema: dict,
    fetch: dict,
    citability: dict,
    brand: dict,
    url: str = "",
) -> list[PageRecommendation]:
    """构建页面级优化建议。

    去重原则：同一 finding 只产出一条 page_rec，带 finding_index；
    由 schema/citability 派生的非 finding 建议 finding_index=-1，
    与 findings 重叠时丢弃。
    """
    recommendations = []
    finding_texts = [f.get("title", "") + " " + f.get("fix", "") for f in findings]

    # 从 findings 中提取页面相关信息
    for idx, f in enumerate(findings):
        page_type = _classify_page_type(f)
        is_new_page = not page_type and any(
            k in f.get("title", "") for k in ["未发布", "缺失", "missing"])
        if not page_type and not is_new_page:
            continue

        rec = PageRecommendation(
            # 新页面建设类：page 标注新增页面路径（从 fix 提取），否则用发现的真实路径
            page=_extract_new_page_path(f) if is_new_page else _infer_page_url(f, page_type, fetch=fetch),
            page_type=page_type or "new-page",
            issue=f.get("title", ""),
            recommendation=f.get("fix", ""),
            priority=SEVERITY_TO_PRIORITY.get(f.get("severity", "Medium"), "P2"),
            impact=_severity_to_impact(f.get("severity", "Medium")),
            effort=_infer_effort(f),
            code_example=_generate_code_example(f, schema, fetch=fetch, url=url),
            finding_index=idx,
        )
        recommendations.append(rec)

    # 补充基于数据的页面建议（跳过与 findings 重叠的，避免同一问题两张卡）
    for rec in _derive_schema_page_recs(schema, fetch, url=url):
        if any(_is_overlapping(rec.issue + rec.recommendation, fx, threshold=0.25) for fx in finding_texts):
            continue
        rec.finding_index = -1
        recommendations.append(rec)
    for rec in _derive_citability_page_recs(citability):
        # citability 派生建议与 finding 的措辞差异大（「可引用性不足」vs「可引用性均分仅 34/100」），
        # 通用重叠检测拦不住。改为话题级检查：已有可引用性 finding 时跳过派生建议。
        if any("可引用性" in fx or "citability" in fx.lower() for fx in finding_texts):
            continue
        rec.finding_index = -1
        recommendations.append(rec)

    # 按优先级排序
    recommendations.sort(key=lambda r: {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(r.priority, 4))

    return recommendations


def _short_title(text: str, max_len: int = 40) -> str:
    """从长行动描述提取短标题：取第一个句号/分号/编号前的内容，超限则截断加省略号。"""
    import re
    t = (text or "").strip()
    # 去掉前导编号（①②③ 或 1. 等）
    t = re.sub(r"^[①②③④⑤⑥⑦⑧⑨\d]+[.、．]?\s*", "", t)
    # 在第一个句读点断句
    for sep in ["。", "；", ";", "；", "，", ","]:
        idx = t.find(sep)
        if 8 <= idx <= max_len:
            return t[:idx]
    if len(t) <= max_len:
        return t
    return t[:max_len].rstrip() + "…"


def _build_priority_actions(
    findings: list,
    quick_wins: list,
    medium_term: list,
    strategic: list,
    fetch: dict = None,
    url: str = "",
) -> list[PriorityAction]:
    """构建优先级行动列表。

    去重原则：同一问题只产出一条 PriorityAction。
    - finding 派生的行动（Critical/High）带 finding_index，是主行动。
    - quick_wins / medium_term / strategic 派生的行动 finding_index=-1；
      若与任何 finding 主题重合（按 _is_overlapping），直接丢弃。
    """
    actions = []

    # 从 findings 生成 P0-P3 行动（title 用 finding 短标题，action 为完整修复建议）
    # 所有 severity 均进 priority_actions，确保按优先级/按页面/按时间三视图覆盖同一全集
    finding_texts = []  # 供重叠检测：title + fix 拼接
    for idx, f in enumerate(findings):
        severity = f.get("severity", "Medium")
        title = f.get("title", "")
        fix = f.get("fix", "")
        finding_texts.append(f"{title} {fix}")
        fix_text = fix or title
        actions.append(PriorityAction(
            action=fix_text,
            title=title,
            priority=SEVERITY_TO_PRIORITY.get(severity, "P2"),
            impact=_severity_to_impact(severity),
            effort=_infer_effort(f),
            owner=_infer_owner(f),
            page=_infer_page_from_finding(f, fetch=fetch),
            code_example=_generate_code_example(f, {}, fetch=fetch, url=url),
            finding_index=idx,
        ))

    # 从 quick_wins / medium_term / strategic 补充（与 findings 重叠的丢弃）
    # 重叠判定阈值降到 0.25：agent 写 quick_wins 时往往会用同一主题关键词
    for a in quick_wins:
        text = a.get("action", "")
        if any(_is_overlapping(text, fx, threshold=0.25) for fx in finding_texts):
            continue
        actions.append(PriorityAction(
            action=text,
            title=_short_title(text),
            priority="P1",
            impact=a.get("impact", "High"),
            effort=a.get("effort", "hours"),
            owner=a.get("owner", "developer"),
            platforms=a.get("platforms", ""),
            finding_index=-1,
        ))

    for a in medium_term:
        text = a.get("action", "")
        if any(_is_overlapping(text, fx, threshold=0.25) for fx in finding_texts):
            continue
        actions.append(PriorityAction(
            action=text,
            title=_short_title(text),
            priority="P2",
            impact=a.get("impact", "High"),
            effort=a.get("effort", "days"),
            owner=a.get("owner", "developer"),
            platforms=a.get("platforms", ""),
            finding_index=-1,
        ))

    for a in strategic:
        text = a.get("action", "")
        if any(_is_overlapping(text, fx, threshold=0.25) for fx in finding_texts):
            continue
        actions.append(PriorityAction(
            action=text,
            title=_short_title(text),
            priority="P3",
            impact=a.get("impact", "High"),
            effort=a.get("effort", "weeks"),
            owner=a.get("owner", "marketing"),
            platforms=a.get("platforms", ""),
            finding_index=-1,
        ))

    # 按优先级排序
    actions.sort(key=lambda a: {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(a.priority, 4))

    return actions


def _action_signature(text: str) -> set:
    """行动文本的关键词签名：提取≥4字的连续中/英文片段，用于重叠检测。"""
    import re
    t = (text or "").lower()
    # 去掉前导编号与标记（①②③、【紧急】【高优】等），避免干扰签名
    t = re.sub(r"[①②③④⑤⑥⑦⑧⑨]|【[^】]*】", " ", t)
    t = re.sub(r"[^0-9A-Za-z一-鿿]+", " ", t)
    tokens = set()
    for w in t.split():
        if len(w) >= 4:
            tokens.add(w)
        elif len(w) >= 2 and re.match(r"^[一-鿿]+$", w):
            tokens.add(w)
    # 关键实体词（JSON-LD/FAQ/Organization 等短词）强制入签
    for kw in ["json-ld", "organization", "faq", "warranty", "llms.txt", "person", "canonical", "hreflang", "url", "安全头", "标题", "短链"]:
        if kw in t:
            tokens.add(kw)
    return tokens


def _is_overlapping(a_text: str, b_text: str, threshold: float = 0.5) -> bool:
    """两条行动是否指向同一修复：较短文本的关键词被较长文本覆盖的比例超阈值即视为重叠。

    用包含度（|a∩b| / |shorter|）而非 Jaccard——finding 的 fix 通常远长于
    quick_win 的一句话，Jaccard 分母会被长文本稀释导致漏判。
    """
    sa, sb = _action_signature(a_text), _action_signature(b_text)
    if not sa or not sb:
        return False
    shorter, longer = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    coverage = len(shorter & longer) / len(shorter)
    return coverage >= threshold


def _build_roadmap(
    quick_wins: list,
    medium_term: list,
    strategic: list,
    findings: list,
    priority_actions: list,
) -> list[RoadmapPhase]:
    """构建 30/60/90 天 Roadmap。

    每阶段 actions 不再复制完整行动正文，而是「短描述 + 引用编号」：
    - 若该行动派生自 finding，记录为 (finding_index, 短描述) 由模板渲染为「→ P01」
    - 若是独立的非 finding 行动（quick_win/medium_term/strategic），记录为 (-1, 短描述)
    这样 Roadmap 与 Top 优先事项 / 行动计划 通过 finding_index 关联，不重复正文。
    """
    roadmap = []

    # 按 finding_index 查找 priority_action：finding 派生行动的 P 级与短描述以 priority_action 为准
    pa_by_finding = {pa.finding_index: pa for pa in priority_actions if pa.finding_index >= 0}

    # 已入路线图的行动签名（跨阶段去重：同一建议不应同时出现在 30/60/90 天）
    # 预填所有 finding 的 title+fix 签名——quick_wins/medium_term/strategic 与任何
    # finding 主题重叠时立即过滤，不等 finding 到达对应阶段才拦截，避免同主题重复出现。
    used_signatures = [f.get("title", "") + " " + f.get("fix", "") for f in findings]
    # 已入路线图的 finding_index（跨阶段去重）
    used_finding_indices = set()

    def _push(bucket, owners, text, owner, finding_index=-1, full_text=""):
        # finding 派生：权威项，只用 finding_index 去重，不做文本重叠检查
        if finding_index >= 0:
            if finding_index in used_finding_indices:
                return False
        else:
            # 非 finding 派生（quick_win/medium_term/strategic）：与所有 finding 签名
            # + 已入路线图的非 finding 签名做重叠检查
            check_text = full_text or text
            if any(_is_overlapping(check_text, u, threshold=0.25) for u in used_signatures):
                return False
        bucket.append((finding_index, text))
        owners.add(owner)
        if finding_index >= 0:
            used_finding_indices.add(finding_index)
        else:
            used_signatures.append(full_text or text)
        return True

    # Phase 1: 30 天 — 基础修复 + 速赢
    phase1_actions = []
    phase1_owners = set()

    # P0 问题必须在前 30 天解决
    for idx, f in enumerate(findings):
        if f.get("severity") == "Critical":
            pa = pa_by_finding.get(idx)
            short = (pa.title if pa else None) or f.get("title", "")[:40]
            full = f.get("title", "") + " " + f.get("fix", "")
            _push(phase1_actions, phase1_owners, short, _infer_owner(f), finding_index=idx, full_text=full)

    # quick_wins 放入前 30 天（跳过上文 priority_actions 已覆盖的）
    for a in quick_wins[:3]:
        _push(phase1_actions, phase1_owners, a.get("action", "")[:60], a.get("owner", "developer"))

    if phase1_actions:
        roadmap.append(RoadmapPhase(
            phase="30 天",
            title="基础修复与速赢",
            description="解决阻碍 GEO 基础能力的紧急问题，建立技术底座",
            actions=phase1_actions,
            owner=_format_owners(phase1_owners),
        ))

    # Phase 2: 60 天 — 核心优化
    phase2_actions = []
    phase2_owners = set()

    for a in quick_wins[3:]:
        _push(phase2_actions, phase2_owners, a.get("action", "")[:60], a.get("owner", "developer"))

    for a in medium_term[:3]:
        _push(phase2_actions, phase2_owners, a.get("action", "")[:60], a.get("owner", "developer"))

    # P1 问题在 60 天内解决
    for idx, f in enumerate(findings):
        if f.get("severity") == "High":
            pa = pa_by_finding.get(idx)
            short = (pa.title if pa else None) or f.get("title", "")[:40]
            _push(phase2_actions, phase2_owners, short, _infer_owner(f), finding_index=idx)

    if phase2_actions:
        roadmap.append(RoadmapPhase(
            phase="60 天",
            title="核心优化与内容建设",
            description="提升内容可引用性、完善 Schema 覆盖",
            actions=phase2_actions,
            owner=_format_owners(phase2_owners),
        ))

    # Phase 3: 90 天 — 长期建设
    phase3_actions = []
    phase3_owners = set()

    for a in medium_term[3:]:
        _push(phase3_actions, phase3_owners, a.get("action", "")[:60], a.get("owner", "developer"))

    for a in strategic:
        _push(phase3_actions, phase3_owners, a.get("action", "")[:60], a.get("owner", "marketing"))

    # P2/P3 优化项
    for idx, f in enumerate(findings):
        if f.get("severity") in ("Medium", "Low"):
            pa = pa_by_finding.get(idx)
            short = (pa.title if pa else None) or f.get("title", "")[:40]
            _push(phase3_actions, phase3_owners, short, _infer_owner(f), finding_index=idx)

    if phase3_actions:
        roadmap.append(RoadmapPhase(
            phase="90 天",
            title="权威建设与长期增长",
            description="建立第三方权威信号、持续内容优化、知识图谱建设",
            actions=phase3_actions,
            owner=_format_owners(phase3_owners),
        ))

    return roadmap


def _decide_chart_visibility(
    components: list,
    citability: dict,
    bots: dict,
    schema: dict,
    brand: dict,
    fetch: dict,
    technical: dict,
) -> dict:
    """决定哪些图表应该显示 — 只有数据足够且有意义时才显示"""
    visibility = {}

    # GEO Score Gauge: 有分数时显示
    visibility["gauge"] = len(components) > 0 and any(c.get("measured", False) for c in components)

    # Radar: 至少 3 个已测维度时显示
    measured = [c for c in components if c.get("measured", False)]
    visibility["radar"] = len(measured) >= 3

    # Waterfall: 与雷达相同条件
    visibility["waterfall"] = len(measured) >= 3

    # Dimension Bars: 有五维度数据时显示
    blocks = citability.get("all_blocks", [])
    visibility["dimension_bars"] = len(blocks) > 0 and any(b.get("breakdown") for b in blocks)

    # Scatter: 有多个段落且分数有差异时显示
    scores = [b.get("total_score", 0) for b in blocks]
    visibility["scatter"] = len(blocks) >= 4 and len(set(scores)) > 1

    # Bot Heatmap: 有实测探针时显示
    probes = bots.get("probes", [])
    visibility["bot_heatmap"] = len(probes) >= 3

    # Schema Status: 有 Schema 清单时显示
    inv = schema.get("inventory", [])
    visibility["schema_status"] = len(inv) > 0

    # Freshness: 有日期数据时显示
    fr = fetch.get("freshness", {})
    visibility["freshness"] = fr.get("age_days") is not None

    # Security Matrix: 有安全头数据时显示
    matrix = technical.get("security_matrix", [])
    visibility["security_matrix"] = len(matrix) > 0

    return visibility


# ── 辅助函数 ──────────────────────────────────────────────

def _classify_page_type(finding: dict) -> str:
    """根据 finding 内容分类页面类型"""
    title = finding.get("title", "").lower()
    evidence = finding.get("evidence", "").lower()
    layer = finding.get("layer", "")

    # 「未发布/缺失某页」是站点级问题，不归到任何现有页面
    if any(k in title for k in ["未发布", "缺失", "missing"]) and any(
            k in title + evidence for k in ["页", "page", "faq", "support", "warranty"]):
        return ""

    # 首页相关
    if any(k in title + evidence for k in ["首页", "homepage", "home page", "head"]):
        return "homepage"

    # 产品页相关
    if any(k in title + evidence for k in ["产品", "product", "sku", "商品"]):
        return "product"

    # 博客/文章页
    if any(k in title + evidence for k in ["博客", "blog", "文章", "article", "freshness", "新鲜度"]):
        return "blog"

    # 关于页
    if any(k in title + evidence for k in ["关于", "about", "品牌", "brand"]):
        return "about"

    # 联系页
    if any(k in title + evidence for k in ["联系", "contact"]):
        return "contact"

    # 分类页
    if any(k in title + evidence for k in ["分类", "category", "列表"]):
        return "category"

    # 模板层问题通常影响多页面
    if layer == "template":
        return "homepage"  # 默认为首页

    return ""


def _discover_page_url(fetch: dict, page_type: str) -> str:
    """从 fetch.internal_links 发现真实页面 URL（避免写死 /blogs/ 这类猜测路径）。

    返回站点相对路径；发现不了返回空串（由调用方决定兜底）。
    匹配首段路径前缀（/about-us 命中 about，/contact-us 命中 contact）。
    """
    from urllib.parse import urlparse
    links = (fetch or {}).get("internal_links") or []
    # 页面类型 → URL 路径关键词（优先级从高到低）
    patterns = {
        "blog":     ["news", "blog", "article", "insights", "resources"],
        "about":    ["about"],
        "contact":  ["contact"],
        "product":  ["product"],
        "category": ["category", "collection", "collections"],
        "faq":      ["faq"],
        "support":  ["support"],
    }
    for kw in patterns.get(page_type, []):
        # 首段路径以关键词开头（about-us / about / aboutus 均命中）
        matches = []
        for l in links:
            path = urlparse(l.get("url", "")).path.rstrip("/")
            first = path.strip("/").split("/")[0] if path.strip("/") else ""
            if first == kw or first.startswith(kw + "-") or first.startswith(kw + "_"):
                matches.append(path)
        if matches:
            # 取最短路径（列表页优于详情页）
            return min(matches, key=len) + "/"
    return ""


def _extract_new_page_path(finding: dict) -> str:
    """从「未发布 /faq 页」类 finding 的 fix 中提取要新建的页面路径。"""
    import re
    fix = finding.get("fix", "")
    m = re.search(r"(/[a-z][a-z0-9-]{1,20})", fix.lower())
    return m.group(1) if m else ""


def _infer_page_url(finding: dict, page_type: str, fetch: dict = None) -> str:
    """推断页面 URL：优先从内链发现真实路径，发现不了用静态映射兜底。page_type 为空返回空串。"""
    if not page_type:
        return ""
    discovered = _discover_page_url(fetch or {}, page_type)
    if discovered:
        return discovered
    type_to_url = {
        "homepage": "/",
        "product": "/products/",
        "blog": "/news/",
        "about": "/about/",
        "contact": "/contact/",
        "category": "/collections/",
    }
    return type_to_url.get(page_type, "")


def _infer_effort(finding: dict) -> str:
    """推断工作量"""
    severity = finding.get("severity", "Medium")
    fix = finding.get("fix", "").lower()

    # 根据修复建议推断工作量
    if any(k in fix for k in ["添加", "注入", "配置", "修改", "补"]):
        if any(k in fix for k in ["json-ld", "schema", "meta", "响应头", "header"]):
            return "hours"
        if any(k in fix for k in ["创建", "起草", "wikipedia", "wikidata"]):
            return "days"

    if severity == "Critical":
        return "hours"
    elif severity == "High":
        return "days"
    elif severity == "Medium":
        return "days"
    else:
        return "weeks"


def _infer_owner(finding: dict) -> str:
    """推断负责人"""
    fix = finding.get("fix", "").lower()
    title = finding.get("title", "").lower()

    # 内容相关
    if any(k in fix + title for k in ["内容", "文章", "博客", "blog", "author", "作者", "署名", "tl;dr"]):
        return "content"

    # 市场/品牌相关
    if any(k in fix + title for k in ["wikipedia", "wikidata", "品牌", "brand", "第三方", "权威", "digital pr", "reddit", "youtube", "linkedin", "crunchbase"]):
        return "marketing"

    # 默认为开发
    return "developer"


def _infer_page_from_finding(finding: dict, fetch: dict = None) -> str:
    """从 finding 提取关联页面（优先从内链发现真实路径）"""
    page_type = _classify_page_type(finding)
    return _infer_page_url(finding, page_type, fetch=fetch)


def _severity_to_impact(severity: str) -> str:
    """严重度转影响"""
    return {"Critical": "High", "High": "High", "Medium": "Med", "Low": "Low"}.get(severity, "Med")


def _derive_next_step(findings: list, quick_wins: list) -> str:
    """推导推荐下一步"""
    # 检查是否有 P0 问题
    has_critical = any(f.get("severity") == "Critical" for f in findings)
    has_high = any(f.get("severity") == "High" for f in findings)

    if has_critical:
        return "优先解决阻碍 GEO 基础能力的紧急问题，再进入内容质量与 Schema 结构化建设。"
    elif has_high:
        return "优先完成基础技术修复和实体定义，再进入产品内容深度与可引用性建设。"
    else:
        return "技术基础良好，建议聚焦内容可引用性与品牌权威信号建设。"


def _derive_schema_page_recs(schema: dict, fetch: dict, url: str = "") -> list[PageRecommendation]:
    """基于 Schema 数据推导页面建议"""
    recs = []

    # 检查首页 Schema
    if schema and not schema.get("has_organization"):
        recs.append(PageRecommendation(
            page="/",
            page_type="homepage",
            issue="首页缺少 Organization 实体 Schema",
            recommendation="在首页 <head> 中添加 Organization JSON-LD，包含 name/url/logo/sameAs",
            priority="P1",
            impact="High",
            effort="hours",
            code_example=_organization_schema_example(url=url, name=_site_name_from_fetch(fetch, url)),
        ))

    # 检查 Product Schema
    inv = schema.get("inventory", [])
    product_status = next((i for i in inv if i.get("schema_type") == "Product"), None)
    if product_status and product_status.get("status") == "Missing":
        recs.append(PageRecommendation(
            page="/products/",
            page_type="product",
            issue="产品页缺少 Product Schema",
            recommendation="为每个产品页添加 Product JSON-LD（name/image/description/offers/aggregateRating）",
            priority="P1",
            impact="High",
            effort="hours",
            code_example=_product_schema_example(url=url, brand=_site_name_from_fetch(fetch, url)),
        ))

    return recs


def _derive_citability_page_recs(citability: dict) -> list[PageRecommendation]:
    """基于可引用性数据推导页面建议"""
    recs = []

    avg_score = citability.get("average_citability_score", 0)
    if avg_score < 50:
        recs.append(PageRecommendation(
            page="/",
            page_type="homepage",
            issue=f"首页内容可引用性不足（均分 {avg_score}/100）",
            recommendation="增加含具体数据、独立测试结果、使用场景的可引用段落（每段 80-150 词）",
            priority="P1",
            impact="High",
            effort="days",
        ))

    return recs


def _site_name_from_fetch(fetch: dict, url: str = "") -> str:
    """从 fetch 数据提取真实站点名：优先 logo alt → title 尾段 → h1 → 域名。"""
    if fetch:
        for img in fetch.get("images") or []:
            alt = (img.get("alt") or "").strip()
            # logo 类图片的 alt 通常是公司名（含 Co., Ltd. / Inc. 等）
            if alt and any(k in alt for k in ["Co.", "Ltd", "Inc", "LLC", "GmbH", "Company", "有限", "科技", "集团"]):
                return alt
        h1 = (fetch.get("h1_tags") or [""])[0]
        if h1:
            # H1 形如 "Fume Extractor... - Guangdong Newoxy Purification Technology"，取尾段
            parts = [p.strip() for p in h1.split(" - ")]
            if len(parts) > 1 and parts[-1]:
                return parts[-1]
        title = fetch.get("title") or ""
        if " - " in title:
            parts = [p.strip() for p in title.split(" - ")]
            if parts[-1]:
                return parts[-1]
    # 回退：域名去 TLD 首字母大写
    if url:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.replace("www.", "")
        return host.split(".")[0].capitalize()
    return "Your Brand"


def _generate_code_example(finding: dict, schema: dict, fetch: dict = None, url: str = "") -> str:
    """根据 finding 生成代码示例"""
    title = finding.get("title", "").lower()
    fix = finding.get("fix", "").lower()

    # Organization Schema
    if "organization" in title + fix and "json-ld" in fix:
        return _organization_schema_example(url=url, name=_site_name_from_fetch(fetch or {}, url))

    # Product Schema
    if "product" in title + fix and "json-ld" in fix:
        return _product_schema_example(url=url, brand=_site_name_from_fetch(fetch or {}, url))

    # robots.txt
    if "robots" in title + fix:
        return _robots_txt_example()

    # 安全头
    if any(k in title + fix for k in ["referrer-policy", "permissions-policy", "安全头"]):
        return _security_headers_example()

    return ""


def _organization_schema_example(url: str = "", name: str = "") -> str:
    """Organization JSON-LD 示例（参数化真实站点值）"""
    site_url = url.rstrip("/") if url else "https://www.example.com"
    site_name = name or "Your Brand"
    return f'''<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "Organization",
  "name": "{site_name}",
  "url": "{site_url}",
  "logo": "{site_url}/logo.png",
  "sameAs": [
    "https://www.linkedin.com/company/yourbrand",
    "https://twitter.com/yourbrand",
    "https://www.youtube.com/yourbrand"
  ]
}}
</script>'''


def _product_schema_example(url: str = "", brand: str = "") -> str:
    """Product JSON-LD 示例（参数化真实站点值）"""
    site_url = url.rstrip("/") if url else "https://www.example.com"
    brand_name = brand or "Your Brand"
    return f'''<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "Product",
  "name": "Product Name",
  "image": "{site_url}/product.jpg",
  "description": "Product description",
  "brand": {{ "@type": "Brand", "name": "{brand_name}" }},
  "offers": {{
    "@type": "Offer",
    "price": "99.99",
    "priceCurrency": "USD"
  }},
  "aggregateRating": {{
    "@type": "AggregateRating",
    "ratingValue": "4.5",
    "reviewCount": "128"
  }}
}}
</script>'''


def _robots_txt_example() -> str:
    """robots.txt 示例"""
    return '''User-agent: GPTBot
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: *
Allow: /'''


def _security_headers_example() -> str:
    """安全头配置示例"""
    return '''# Nginx 配置示例
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;'''


def _format_owners(owners: set) -> str:
    """格式化负责人列表"""
    owner_map = {
        "developer": "开发",
        "content": "内容",
        "marketing": "市场",
    }
    return " / ".join(owner_map.get(o, o) for o in sorted(owners))
