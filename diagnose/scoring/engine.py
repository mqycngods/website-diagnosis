"""ScoreEngine — 确定性评分打底 + agent override。

职责：
  1. 从采集 JSON 计算各维度的确定性 baseline 分（任何机器可复现）
  2. agent 的 scores.json 作为 override 层：有 agent 分用 agent 分，
     无则用 baseline；最终分记录来源（deterministic / agent / unmeasured）
  3. 按权重合成 GEO 总分

评分规则集中在此，渲染层（render/build.py）不再含评分逻辑。
"""
from __future__ import annotations

from diagnose.config import cfg
from diagnose.models import ScoreCard

# 五维中文名（权重数值在 config/default.yaml 的 scoring.weights）
DIM_NAMES = {
    "citability": "内容引用度",
    "content": "内容质量",
    "technical": "技术基础",
    "schema": "品牌识别与 Schema",
    "platform": "爬虫可达性",
}

# 兼容：旧代码引用的 WEIGHTS（key → (中文名, 权重)）
def _load_weights() -> dict:
    w = cfg("scoring.weights", {}) or {}
    return {k: (name, float(w.get(k, 0.0))) for k, name in DIM_NAMES.items()}

WEIGHTS = _load_weights()

SCORE_LABELS = ("Excellent", "Good", "Fair", "Poor", "Critical")


def score_label(score: int) -> str:
    return ("Excellent" if score >= 90 else "Good" if score >= 75 else
            "Fair" if score >= 60 else "Poor" if score >= 40 else "Critical")


def schema_baseline(schema: dict) -> int:
    """schema.json → 0-100：关键类型覆盖 + sameAs 完整性。"""
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


def compute_baselines(*, citability: dict, bots: dict, technical: dict,
                      schema: dict) -> dict:
    """从采集数据计算确定性 baseline。无数据的维度返回 None（未测量）。"""
    def ok(d):
        return isinstance(d, dict) and "error" not in d and bool(d)

    cit = citability.get("average_citability_score") if ok(citability) else None
    bot = bots.get("overall_score") if ok(bots) else None
    tech = technical.get("security_score") if ok(technical) else None
    sch = schema_baseline(schema) if ok(schema) else None
    return {
        "citability": cit,
        "content": cit,        # 内容质量以可引用性为代理回退
        "technical": tech,
        "schema": sch,
        "platform": bot,       # 平台就绪以爬虫实测为代理回退
    }


class ScoreEngine:
    """维度分合成：agent override 优先，确定性 baseline 兜底。"""

    def __init__(self, weights: dict = None):
        self.weights = weights or WEIGHTS

    def score(self, baselines: dict, agent_scores: dict = None) -> list[ScoreCard]:
        """baselines: compute_baselines 的输出；agent_scores: scores.json（可空）。

        兼容两种 dimensions 结构：
        - dict: {"citability": {"score": 80, ...}, ...}（期望格式）
        - list: [{"name": "可引用性", "score": 80, ...}, ...]（Agent 常见错误，自动转换）
        """
        dims = {}
        if isinstance(agent_scores, dict) and "error" not in agent_scores:
            raw_dims = agent_scores.get("dimensions") or {}
            # 兼容 list 结构：Agent 常把 dimensions 写成 [{"name": "可引用性", ...}]
            if isinstance(raw_dims, list):
                name_map = {
                    "可引用性": "citability",
                    "AI 可达性": "ai_access",
                    "技术基础": "technical",
                    "结构化数据": "schema",
                    "内容质量": "content",
                }
                for item in raw_dims:
                    if isinstance(item, dict):
                        zh_name = item.get("name", "")
                        en_key = name_map.get(zh_name)
                        if en_key:
                            dims[en_key] = item
            elif isinstance(raw_dims, dict):
                dims = raw_dims

        cards = []
        for key, (name, weight) in self.weights.items():
            baseline = baselines.get(key)
            card = ScoreCard(key=key, name=name, weight=weight,
                             baseline_score=baseline)
            d = dims.get(key)
            if isinstance(d, dict) and d.get("score") is not None:
                card.score = int(d["score"])
                card.note = d.get("note") or ""
                card.source = "agent"
                card.measured = True
            elif baseline is not None:
                card.score = int(baseline)
                card.source = "deterministic"
                card.measured = True
            else:
                card.score = 0
                card.source = "unmeasured"
                card.measured = False
            cards.append(card)
        return cards

    def geo_score(self, cards: list[ScoreCard]) -> int:
        measured = [c for c in cards if c.measured]
        if not measured:
            return 0
        tw = sum(c.weight for c in measured) or 1.0
        return round(sum(c.score * c.weight for c in measured) / tw)
