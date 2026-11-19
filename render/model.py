"""ReportData — 渲染前最后一个结构化对象（精简版）。

报告域 dataclass（TopPriority/PageRecommendation/PriorityAction/RoadmapPhase/
ExecutiveSummary）的唯一定义在 diagnose/models.py，这里只做 import 转发，
保持既有 `from render.model import TopPriority` 的调用不断。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

from diagnose.models import (  # noqa: F401  (re-export)
    TopPriority, PageRecommendation, PriorityAction, RoadmapPhase, ExecutiveSummary,
)


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
    # 来源：agent（scores.json 写的）/ analyzer（服务端分析器补充）。
    # analyzer 项只进附录「服务端补充发现」，不进行动清单与执行清单对齐范围，
    # 否则报告会多出 agent 从未见过、清单里没有的条目（两份产物不同源）。
    source: str = "agent"
    # 对齐 p0_blockers 的机器可读 key（agent 从 site_analyze 返回的 blocker 复制）。
    # 替代脆弱的标题正则映射：agent 自报 kind 命中 blocker 时直接用，
    # 正则只做兜底（历史三次因措辞差异漏配导致 Critical 被误降）。
    kind: str = ""
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


# ── 报告域模型（TopPriority / PageRecommendation / PriorityAction /
#    RoadmapPhase / ExecutiveSummary）已移至 diagnose/models.py 并在顶部 re-export。


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

    # P0 阻塞判定（diagnose/blockers.py 确定性算出的清单）。
    # 报告横幅的配色与「查看 P0 清单」入口据此判断，而不是数 findings 里的
    # Critical——分析器的形式缺失可以报 Critical，但不等于 P0 阻塞。
    # None 表示旧数据/未跑 analyze，模板退回看 findings。
    p0_blockers: list = field(default_factory=list)

    # ── 客户报告新增字段 ────────────────────────────────────
    executive_summary: ExecutiveSummary = field(default_factory=ExecutiveSummary)
    page_recommendations: list = field(default_factory=list)   # list[PageRecommendation]
    priority_actions: list = field(default_factory=list)       # list[PriorityAction]
    roadmap: list = field(default_factory=list)                # list[RoadmapPhase]
    chart_visibility: dict = field(default_factory=dict)       # 图表显示决策

    chart_data: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)
