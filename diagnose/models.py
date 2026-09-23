"""统一数据模型 — 全项目单一事实来源。

分层：
  诊断域（诊断引擎产出，与渲染无关）：
    Evidence / Issue / Finding / Recommendation / ScoreCard
  报告域（客户报告数据对象，render 与 report_builder 共用）：
    TopPriority / PageRecommendation / PriorityAction / RoadmapPhase / ExecutiveSummary

render/model.py 的 ReportData 是渲染视图模型，留在渲染层；
这里不定义 ReportData。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

# ── 枚举（机器值一律英文，展示层负责中文伴生） ──────────────
SEVERITIES = ("Critical", "High", "Medium", "Low")
CONFIDENCES = ("Confirmed", "Likely", "Hypothesis")
LAYERS = ("domain", "template", "editorial", "page")
PRIORITIES = ("P0", "P1", "P2", "P3")
OWNERS = ("developer", "content", "marketing", "seo", "product", "client")

SEVERITY_TO_PRIORITY = {"Critical": "P0", "High": "P1", "Medium": "P2", "Low": "P3"}


# ── 诊断域 ────────────────────────────────────────────────

@dataclass
class Evidence:
    """一条证据：任何诊断结论必须能回溯到观测值。

    type:     structured_data / http_header / html / robots_txt / sitemap /
              ua_probe / external_api / text_content
    location: 取值位置（"schema.Organization.sameAs" / "HTML <title>"）
    source:   采集来源（page_html / robots.txt / ua_replay / wikidata_api）
    url:      观测页面
    raw:      原始片段（截断保存，供报告展示与调试）
    """
    type: str
    value: object = None
    location: str = ""
    source: str = ""
    url: str = ""
    raw: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class Issue:
    """checks/analyzers 产出的结构化问题（Finding 的前体）。

    id:       稳定规则 ID（GEO-ENT-002），由 checks 元数据分配
    category: 分析方向（entity_graph / consistency / coverage / ...）
    kind:     机器可读问题类型（organization_no_sameas）
    evidence: {signal_name: {value, location, raw}} —— 结构化证据，非自由文本
    """
    id: str = ""
    category: str = ""
    kind: str = ""
    severity: str = "Medium"
    confidence: str = "Confirmed"
    layer: str = "page"
    title: str = ""
    evidence: dict = field(default_factory=dict)
    impact: str = ""
    fix: str = ""
    page_url: str = ""
    affected_pages: list = field(default_factory=list)
    page_count: int = 1
    is_template_level: bool = False

    def to_dict(self):
        return asdict(self)


@dataclass
class Finding:
    """诊断结论（报告与评分的统一货币）。

    Finding ≠ Recommendation：
      finding 回答「什么问题、多严重、证据是什么」
      recommendation（fix 字段 + Recommendation 对象）回答「怎么修」
    evidence_text 是面向客户的中文叙述；evidence 是结构化数据。
    """
    id: str = ""                     # GEO-XXX-NNN；agent 手写 finding 可为空
    category: str = ""
    kind: str = ""
    title: str = ""
    evidence: dict = field(default_factory=dict)   # 结构化证据
    evidence_text: str = ""                        # 中文叙述（报告展示用）
    impact: str = ""
    fix: str = ""
    severity: str = "Medium"
    confidence: str = "Confirmed"
    layer: str = "page"
    page_url: str = ""
    affected_pages: list = field(default_factory=list)
    page_count: int = 1
    is_template_level: bool = False
    source: str = ""                 # "analyzer:entity_graph" / "agent" / "fallback"
    # 渲染层注入
    code_example: str = ""
    owner_zh: str = ""
    severity_zh: str = ""
    confidence_zh: str = ""
    layer_zh: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class Recommendation:
    """优化建议 — 由 RecommendationEngine 从 Finding 派生。

    与 Finding 分离：一个 finding 可派生多条 recommendation（拆分时），
    多条 finding 也可合并为一条 recommendation（同根因）。
    """
    id: str = ""                     # REC-001
    finding_ids: list = field(default_factory=list)
    title: str = ""
    action: str = ""
    priority: str = "P2"             # P0/P1/P2/P3
    impact: str = "Med"              # High/Med/Low
    effort: str = "hours"            # minutes/hours/days/weeks
    owner: str = "developer"         # developer/content/marketing/seo/product/client
    estimated_time: str = ""         # 中文自然语言（"1-2 小时"），agent 可覆盖
    dependencies: list = field(default_factory=list)   # 前置 REC id
    pages: list = field(default_factory=list)
    code_example: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class ScoreCard:
    """评分卡：确定性 baseline + agent override 双轨。

    baseline_score: 脚本从采集数据计算的确定性分（可复现）
    score:          最终分（agent override 优先，否则 = baseline）
    source:         "deterministic" / "agent" / "unmeasured"
    """
    key: str
    name: str = ""
    weight: float = 0.0
    score: int = 0
    baseline_score: int = None
    source: str = "unmeasured"
    measured: bool = False
    note: str = ""

    def to_dict(self):
        return asdict(self)


# ── 报告域（render/build.py 与 recommendations 引擎共用，唯一定义） ──

@dataclass
class TopPriority:
    index: int
    title: str
    description: str
    impact: str
    effort: str
    owner: str
    impact_zh: str = ""
    effort_zh: str = ""
    owner_zh: str = ""


@dataclass
class PageRecommendation:
    page: str
    page_type: str                   # homepage / product / category / about / blog / faq
    issue: str
    recommendation: str
    priority: str
    impact: str
    effort: str
    code_example: str = ""
    finding_index: int = -1          # 派生自 findings[i]；非 finding 派生为 -1


@dataclass
class PriorityAction:
    action: str
    priority: str
    impact: str
    effort: str
    owner: str
    platforms: str = ""
    page: str = ""
    code_example: str = ""
    title: str = ""
    finding_index: int = -1
    impact_zh: str = ""
    effort_zh: str = ""
    owner_zh: str = ""


@dataclass
class RoadmapPhase:
    phase: str                       # "30 天" / "60 天" / "90 天"
    title: str
    description: str
    actions: list = field(default_factory=list)
    owner: str = ""


@dataclass
class ExecutiveSummary:
    geo_score: int = 0
    label: str = ""
    label_zh: str = ""
    overall_assessment: str = ""
    top_priorities: list = field(default_factory=list)
    recommended_next_step: str = ""
