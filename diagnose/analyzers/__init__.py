"""Analyzers — 通用分析器层（checks 规则引擎）。

职责：基于 signals.json 的纯事实，产出结构化 issue。
每个分析器是纯函数：analyze(signals: dict) -> list[Issue]

Issue 统一协议见 bottom 的 make_issue。

新增分析方向 = 新写一个 analyze_*(signals) 函数 + 注册到 REGISTRY。
新增规则 = 在 RULES 中登记 (category, kind) → {id, weight, name}，
make_issue 会自动携带规则 ID（未登记时报 "UNREGISTERED"）。
"""
from __future__ import annotations

# 严重度与类别枚举（与 score.md 对齐）——唯一定义在 diagnose/models.py
from diagnose.models import SEVERITIES, CONFIDENCES as CONFIDENCE, LAYERS  # noqa: F401

# ── 规则注册表：{(category, kind): {id, name, weight}} ──────────────
# id:      稳定规则 ID（报告与执行计划可追溯）
# weight:  该规则在其 category 内的相对权重（供确定性评分扣分用，0-10）
RULES = {
    ("consistency", "author_mismatch"):           {"id": "GEO-CON-001", "weight": 6, "name": "作者署名跨位置不一致"},
    ("consistency", "author_mismatch_template"):  {"id": "GEO-CON-002", "weight": 9, "name": "作者署名模板级系统性冲突"},
    ("consistency", "title_mismatch"):            {"id": "GEO-CON-003", "weight": 2, "name": "title 与 og:title 差异过大"},
    ("consistency", "canonical_og_url_mismatch"): {"id": "GEO-CON-004", "weight": 3, "name": "canonical 与 og:url 不一致"},

    ("entity_graph", "homepage_missing_organization"): {"id": "GEO-ENT-001", "weight": 9, "name": "主页缺 Organization schema"},
    ("entity_graph", "organization_no_sameas"):        {"id": "GEO-ENT-002", "weight": 9, "name": "Organization 缺 sameAs"},
    ("entity_graph", "organization_missing_facts"):    {"id": "GEO-ENT-003", "weight": 4, "name": "Organization 缺关键事实字段"},
    ("entity_graph", "organization_id_inconsistent"):  {"id": "GEO-ENT-004", "weight": 6, "name": "Organization @id 跨页不一致"},
    ("entity_graph", "product_brand_no_type"):         {"id": "GEO-ENT-005", "weight": 6, "name": "Product.brand 缺 @type"},
    ("entity_graph", "product_brand_no_id_ref"):       {"id": "GEO-ENT-006", "weight": 4, "name": "Product.brand 未引用 Organization @id"},
    ("entity_graph", "person_no_worksfor"):            {"id": "GEO-ENT-007", "weight": 2, "name": "Person 缺 worksFor"},
    ("entity_graph", "article_publisher_no_id"):       {"id": "GEO-ENT-008", "weight": 2, "name": "Article.publisher 未关联 Organization"},

    ("coverage", "h1_too_generic"):            {"id": "GEO-COV-001", "weight": 5, "name": "H1 过于通用"},
    ("coverage", "meta_description_missing"):  {"id": "GEO-COV-002", "weight": 3, "name": "缺 meta description"},
    ("coverage", "article_description_empty"): {"id": "GEO-COV-003", "weight": 4, "name": "Article.description 为空"},
    ("coverage", "og_title_missing"):          {"id": "GEO-COV-004", "weight": 1, "name": "缺 og:title"},
    ("coverage", "og_image_missing"):          {"id": "GEO-COV-005", "weight": 1, "name": "缺 og:image"},
    ("coverage", "canonical_missing"):         {"id": "GEO-COV-006", "weight": 3, "name": "缺 canonical"},
    ("coverage", "h1_missing"):                {"id": "GEO-COV-007", "weight": 2, "name": "页面缺 H1"},
    ("coverage", "faq_content_without_schema"): {"id": "GEO-COV-008", "weight": 6, "name": "FAQ 页有内容但缺 FAQPage schema"},

    ("argumentation", "high_absolute_density"):          {"id": "GEO-ARG-001", "weight": 4, "name": "绝对化表述密度高"},
    ("argumentation", "no_authority_external_links"):    {"id": "GEO-ARG-002", "weight": 6, "name": "无独立第三方外链"},
    ("argumentation", "few_authority_external_links"):   {"id": "GEO-ARG-003", "weight": 4, "name": "独立第三方外链偏少"},
    ("argumentation", "low_numeric_density"):            {"id": "GEO-ARG-004", "weight": 4, "name": "具体数字密度低"},

    ("authority", "no_authority_links_on_home"): {"id": "GEO-AUT-001", "weight": 3, "name": "主页未链向权威外部平台"},
}


def rule_id_for(category: str, kind: str) -> str:
    rule = RULES.get((category, kind))
    return rule["id"] if rule else ""


def make_issue(
    category: str,           # 分析方向：consistency / entity_graph / argumentation / ...
    kind: str,               # 具体问题类型：author_mismatch / missing_sameas / ...
    severity: str,           # Critical / High / Medium / Low
    title: str,              # 中文标题（面向客户）
    evidence: dict,          # 证据：{signal_name: {value, location, raw}}
    impact: str = "",        # 中文：对 GEO/AI 的影响
    fix: str = "",           # 中文：可执行修复建议
    confidence: str = "Confirmed",
    layer: str = "page",
    page_url: str = "",
) -> dict:
    """统一 issue 结构。id 由 (category, kind) 在 RULES 中自动解析。"""
    return {
        "id": rule_id_for(category, kind),
        "category": category,
        "kind": kind,
        "severity": severity,
        "confidence": confidence,
        "layer": layer,
        "title": title,
        "evidence": evidence,
        "impact": impact,
        "fix": fix,
        "page_url": page_url,
    }


# 分析器注册表：{name: callable(signals) -> list[Issue]}
# 延迟 import，避免循环依赖
REGISTRY = {}


def register(name: str):
    """装饰器：注册一个分析器。"""
    def deco(fn):
        REGISTRY[name] = fn
        return fn
    return deco


def run_all(signals: dict) -> dict:
    """跑所有已注册的分析器，返回 {analyzer_name: [issue, ...]}"""
    # 触发各模块注册
    from . import consistency  # noqa
    from . import entity_graph  # noqa
    from . import coverage  # noqa
    from . import argumentation  # noqa
    from . import authority  # noqa

    results = {}
    for name, fn in REGISTRY.items():
        try:
            results[name] = fn(signals) or []
        except Exception as e:
            results[name] = [make_issue(
                category=name, kind="analyzer_error", severity="Low",
                title=f"分析器 {name} 运行失败",
                evidence={"error": {"value": str(e), "location": "analyzer"}},
                confidence="Hypothesis", layer="domain",
            )]
    return results
