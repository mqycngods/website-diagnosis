"""ReportData — 渲染前最后一个结构化对象（精简版）。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Finding:
    title: str
    evidence: str
    impact: str
    fix: str = ""
    confidence: str = "Confirmed"      # Confirmed | Likely | Hypothesis
    severity: str = "Medium"           # Critical | High | Medium | Low
    layer: str = ""                    # domain | template | editorial | page
    code_example: str = ""             # 由对应 priority_action 注入（build.py）
    owner_zh: str = ""                 # 负责人中文（build.py 注入）
    # 中文伴生（渲染层填，机器 key 保留英文供 CSS/逻辑）
    severity_zh: str = ""
    confidence_zh: str = ""
    layer_zh: str = ""


@dataclass
class Action:
    action: str
    impact: str = "Med"
    effort: str = "hours"
    owner: str = "developer"
    platforms: str = ""
    impact_zh: str = ""
    effort_zh: str = ""
    owner_zh: str = ""


@dataclass
class ComponentScore:
    key: str
    name: str                          # 中文维度名
    score: int = 0
    weight: float = 0.0
    measured: bool = True
    status_zh: str = ""
    note: str = ""                     # 得分理由（agent 在 scores.json 中提供，≤120 字）


# ── 客户报告数据模型（新增） ─────────────────────────────────

@dataclass
class TopPriority:
    """Top 3 优先事项"""
    index: int                      # 01, 02, 03
    title: str
    description: str
    impact: str
    effort: str
    owner: str
    # 中文伴生（渲染层填）
    impact_zh: str = ""
    effort_zh: str = ""
    owner_zh: str = ""


@dataclass
class PageRecommendation:
    """页面级优化建议"""
    page: str                       # 页面路径或类型
    page_type: str                  # homepage / product / category / about / blog / faq
    issue: str                      # 问题描述
    recommendation: str             # 具体建议
    priority: str                   # P0/P1/P2/P3
    impact: str                     # High/Med/Low
    effort: str                     # minutes/hours/days/weeks
    code_example: str = ""          # 可选的代码示例


@dataclass
class PriorityAction:
    """优先级行动项"""
    action: str
    priority: str                   # P0/P1/P2/P3
    impact: str                     # High/Med/Low
    effort: str                     # minutes/hours/days/weeks
    owner: str                     # developer/content/marketing
    platforms: str = ""
    page: str = ""                  # 关联页面
    code_example: str = ""          # 可选的代码示例
    title: str = ""                 # 短标题（≤40字），为空时模板截断 action

    # 中文伴生
    impact_zh: str = ""
    effort_zh: str = ""
    owner_zh: str = ""


@dataclass
class RoadmapPhase:
    """Roadmap 阶段"""
    phase: str                      # "30 天" / "60 天" / "90 天"
    title: str
    description: str
    actions: list = field(default_factory=list)
    owner: str = ""


@dataclass
class ExecutiveSummary:
    """执行摘要"""
    geo_score: int = 0
    label: str = ""
    label_zh: str = ""
    overall_assessment: str = ""    # 一句话总结
    top_priorities: list = field(default_factory=list)  # list[TopPriority]
    recommended_next_step: str = ""


@dataclass
class ReportData:
    domain: str
    url: str
    audit_date: str
    geo_score: int = 0
    label: str = ""                    # Excellent..Critical
    label_zh: str = ""
    description: str = ""
    components: list = field(default_factory=list)   # list[ComponentScore]

    # 各章原始数据（直接透传诊断 JSON + scores.json）
    citability: dict = field(default_factory=dict)
    bots: dict = field(default_factory=dict)
    robots: dict = field(default_factory=dict)
    schema: dict = field(default_factory=dict)
    fetch: dict = field(default_factory=dict)
    brand: dict = field(default_factory=dict)
    technical: dict = field(default_factory=dict)
    scores: dict = field(default_factory=dict)       # agent 评分产出

    findings: list = field(default_factory=list)     # list[Finding]
    quick_wins: list = field(default_factory=list)   # list[Action]
    medium_term: list = field(default_factory=list)
    strategic: list = field(default_factory=list)
    impact_estimate: str = ""

    # ── 客户报告新增字段 ────────────────────────────────────
    executive_summary: ExecutiveSummary = field(default_factory=ExecutiveSummary)
    page_recommendations: list = field(default_factory=list)   # list[PageRecommendation]
    priority_actions: list = field(default_factory=list)       # list[PriorityAction]
    roadmap: list = field(default_factory=list)                # list[RoadmapPhase]
    chart_visibility: dict = field(default_factory=dict)       # 图表显示决策

    chart_data: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)
