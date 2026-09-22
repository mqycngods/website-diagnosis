"""Analyzers — 通用分析器层。

职责：基于 signals.json 的纯事实，产出结构化 issue。
每个分析器是纯函数：analyze(signals: dict) -> list[Issue]

Issue 统一协议见 bottom 的 make_issue。

新增分析方向 = 新写一个 analyze_*(signals) 函数 + 注册到 REGISTRY。
"""
from __future__ import annotations

# 严重度与类别枚举（与 score.md 对齐）
SEVERITIES = ("Critical", "High", "Medium", "Low")
CONFIDENCE = ("Confirmed", "Likely", "Hypothesis")
LAYERS = ("domain", "template", "editorial", "page")


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
    """统一 issue 结构。"""
    return {
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
