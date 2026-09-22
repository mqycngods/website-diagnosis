"""ECharts 数据预计算 — 模板不做算术。

新增：图表选择逻辑 — 只有数据足够且有意义时才生成图表数据。
数据不足时直接不生成，模板层会显示数字卡片或"未测量"。
"""
from __future__ import annotations

COLORS = {"orange": "#c9552c", "green": "#52765e", "amber": "#a96a34",
          "red": "#a75b4a", "ink": "#27231f", "muted": "#7d7466",
          "line": "#ded6c8", "warm": "#f4efe5", "warm2": "#efe9dc", "ink2": "#4a443b"}

# 可引用性五维度 key → 中文（满分：答案块30·自足25·结构20·统计15·独特10）
_DIM_ZH = {
    "answer_block_quality": "答案块质量",
    "self_containment": "自足性",
    "structural_readability": "结构可读性",
    "statistical_density": "统计密度",
    "uniqueness_signals": "独特信号",
}

# 五维度元数据：含义 / 满分示例 / 改写提示（用于柱状图 hover 与段落表「优化方向」列）
_DIM_META = {
    "answer_block_quality": {
        "max": 30,
        "desc": "段落能否被 AI 直接摘抄为答案。得分点：含「XX 是 XX」定义句、带来源词（根据 / 数据显示 / 研究表明）、清晰句占比（4–25 词/句）。",
        "example": "「LED 显示屏是一种通过发光二极管阵列成像的设备。根据 DSCC 2024 报告，全球市场规模达 680 亿元。」",
        "hint": "补「XX 是 XX」定义句 + 来源词（根据/数据显示）",
    },
    "self_containment": {
        "max": 25,
        "desc": "段落脱离上下文能否独立成立。得分点：长度落在 90–167 字甜区、少代词、含品牌/型号等专有名词。",
        "example": "把「这款产品功耗低」改成「边光 AD-EL-2401200-B LED 模组功耗 180W/㎡」。",
        "hint": "替换代词为具体专有名词，段长调至 90–167 字",
    },
    "structural_readability": {
        "max": 20,
        "desc": "段落的机器可读结构。得分点：平均句长 8–22 字、含序列词（首先/其次/最后）、编号步骤、换行分段。",
        "example": "「选择 LED 显示屏需考虑三点：首先，像素间距决定清晰度；其次，亮度匹配环境；最后，刷新率影响拍摄。」",
        "hint": "拆短句，用「首先/其次/最后」或编号分层",
    },
    "statistical_density": {
        "max": 15,
        "desc": "硬数据浓度。得分点：百分比、金额、量级（N 个/家/人/users/customers）、近年份（2010+）、研究/报告来源词。",
        "example": "「2024 年公司出货量同比增长 37%，海外营收占比 42%，服务客户超过 10 万家。」",
        "hint": "把数字写成「数字+量词+时间+来源」",
    },
    "uniqueness_signals": {
        "max": 10,
        "desc": "独家内容标记。得分点：声明自有数据/研究/调查、用「例如/案例」引用具体项目。",
        "example": "「我们的 2024 客户调研显示，83% 政企客户将可维护性列为首要考量。例如某省级政务园区项目…」",
        "hint": "加「我们的数据/调研」「例如+具体项目名」",
    },
}


def _band(score) -> str:
    if score is None:
        return COLORS["muted"]
    return COLORS["green"] if score >= 75 else "#7a947f" if score >= 60 else COLORS["amber"] if score >= 40 else COLORS["red"]


def should_render_chart(chart_type: str, report) -> bool:
    """
    判断图表是否应该渲染。

    原则：
    - 数据不足时不生成图表
    - 数据只有一个值时不生成图表（无比较关系）
    - 数据来源不明确时不生成图表
    - 图表无法产生结论时不生成
    """
    cv = getattr(report, 'chart_visibility', {})
    return cv.get(chart_type, True)


def compute_charts(report) -> dict:
    """计算图表数据 — 只有满足条件时才生成"""
    cd = {"colors": COLORS}

    measured = [c for c in report.components if c.measured]

    # ── 仪表盘：有分数时显示 ────────────────────────────────
    if should_render_chart("gauge", report) and measured:
        cd["gauge"] = {"score": report.geo_score, "label": report.label_zh or report.label}

    # ── 雷达：至少 3 个已测维度时显示 ──────────────────────
    if should_render_chart("radar", report) and len(measured) >= 3:
        cd["radar"] = {
            "labels": [c.name for c in measured],
            "values": [c.score for c in measured],
            "notes": [c.note for c in measured],
        }

    # ── 权重瀑布：与雷达相同条件 ──────────────────────────
    if should_render_chart("waterfall", report) and len(measured) >= 3:
        tw = sum(c.weight for c in measured) or 1.0
        cum, steps = 0.0, []
        for c in measured:
            v = round(c.score * c.weight / tw, 1)
            steps.append({"name": f"{c.name} ×{c.weight:.2f}", "value": v,
                          "start": round(cum, 1), "end": round(cum + v, 1),
                          "score": c.score, "color": _band(c.score)})
            cum += v
        cd["waterfall"] = {"steps": steps, "total": report.geo_score}

    # ── 可引用性图表 ──────────────────────────────────────
    cit = report.citability
    blocks = cit.get("all_blocks") or []

    # 五维度均值：有五维度数据
    if should_render_chart("dimension_bars", report) and len(blocks) > 0:
        dims = {}
        for b in blocks:
            for k, v in (b.get("breakdown") or {}).items():
                dims.setdefault(k, []).append(v)
        if dims:
            keys = list(dims.keys())
            cd["dimension_bars"] = {
                "labels": [_DIM_ZH.get(k, k) for k in keys],
                "values": [round(sum(v) / len(v), 1) for v in dims.values()],
                "maxima": [_DIM_META.get(k, {}).get("max", 0) for k in keys],
                "descs": [_DIM_META.get(k, {}).get("desc", "") for k in keys],
                "examples": [_DIM_META.get(k, {}).get("example", "") for k in keys],
                "hints": [_DIM_META.get(k, {}).get("hint", "") for k in keys],
            }

    # 散点：至少 4 个段落且分数有差异
    if should_render_chart("scatter", report) and len(blocks) >= 4:
        scores = [b.get("total_score", 0) for b in blocks]
        if len(set(scores)) > 1:
            cd["scatter"] = {"points": [
                {"words": b.get("word_count", 0), "score": b.get("total_score", 0),
                 "heading": (b.get("heading") or "")[:40], "grade": b.get("grade", "F")}
                for b in blocks], "optimal_band": [90, 167]}

    # ── 爬虫图表 ──────────────────────────────────────────
    bots = report.bots
    probes = bots.get("probes") or []

    # 热力矩阵：至少 3 个探针
    if should_render_chart("bot_heatmap", report) and len(probes) >= 3:
        order = {"live-retrieval": 0, "search-index": 1, "traditional-search": 2, "training": 3}
        probes = sorted(probes, key=lambda p: (order.get(p.get("class"), 9), p.get("bot")))
        state_color = {"allowed": COLORS["green"], "blocked": COLORS["red"],
                       "stripped": COLORS["amber"], "payment": "#5b7ba7"}
        rows, labels = [], []
        for i, p in enumerate(probes):
            if p.get("payment_required"):
                state = "payment"
            elif p.get("blocked") and p.get("block_reason") == "content_stripped":
                state = "stripped"
            elif p.get("blocked"):
                state = "blocked"
            else:
                state = "allowed"
            labels.append(f"{p.get('bot')}\n({p.get('bot_class_zh') or p.get('class')})")
            rows.append({"x": i, "y": 0, "color": state_color[state], "bot": p.get("bot"),
                         "status": p.get("status"), "similarity": p.get("similarity"),
                         "reason": p.get("block_reason"), "state": state})
        cd["bot_heatmap"] = {"bots": labels, "rows": rows}

    # ── Schema 图表 ────────────────────────────────────────
    inv = report.schema.get("inventory") or []

    # Schema 状态：有清单时显示（NotSampled 不计入缺失分母——未知不断言）
    if should_render_chart("schema_status", report) and len(inv) > 0:
        cd["schema_status"] = {"present": sum(1 for i in inv if i.get("status") == "Present"),
                               "missing": sum(1 for i in inv if i.get("status") == "Missing"),
                               "not_sampled": sum(1 for i in inv if i.get("status") == "NotSampled")}

    # ── 新鲜度图表 ────────────────────────────────────────
    fr = (report.fetch.get("freshness") or {})

    # 新鲜度时间轴：有日期数据时显示
    if should_render_chart("freshness", report) and fr.get("age_days") is not None:
        cd["freshness"] = {
            "age_days": fr["age_days"], "tier": fr.get("tier_zh") or fr.get("tier"),
            "best_date": fr.get("best_date"), "source": fr.get("source"),
            "fresh_boundary": 90, "stale_boundary": 365,
        }

    # ── 技术图表 ──────────────────────────────────────────
    tech = report.technical
    matrix = tech.get("security_matrix") or []

    # 安全头矩阵：有数据时显示
    if should_render_chart("security_matrix", report) and len(matrix) > 0:
        cd["security_matrix"] = {"headers": matrix,
                                  "https": tech.get("https"),
                                  "score": tech.get("security_score")}

    return cd
