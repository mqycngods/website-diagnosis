"""论证质量分析：内容是否有具体数字、外部权威引用、平衡的对比。

脚本只做「表面信号」采集（数字计数 / 绝对化词 / 权威外链计数），
深度语义判断交给 agent 在 Phase 2 用 LLM 完成。
"""
from __future__ import annotations

from . import make_issue, register


def _v(sig):
    return (sig or {}).get("value")


@register("argumentation")
def analyze(signals: dict) -> list:
    issues = []
    main = signals.get("main_page") or {}
    content = main.get("content") or {}
    meta = main.get("meta") or {}
    url = _v(meta.get("url")) or ""

    # ─── 规则 1：绝对化表述密度 ──────────────────────────────────
    abs_terms = _v(content.get("absolute_terms")) or []
    word_count = _v(content.get("word_count")) or 0

    if abs_terms and word_count > 0:
        # 每 500 词超过 1.5 个绝对化词 = 高密度（阈值从 2 降到 1.5，对营销型首页更敏感）
        density = len(abs_terms) / max(word_count / 500, 1)
        if density >= 1.5:
            issues.append(make_issue(
                category="argumentation",
                kind="high_absolute_density",
                severity="Medium",
                title=f"正文绝对化表述密度高（{len(abs_terms)} 处 / {word_count} 词）",
                evidence={
                    "absolute_terms": {"value": [t["term"] for t in abs_terms[:10]],
                                       "location": "text_content"},
                    "word_count": {"value": word_count, "location": "page"},
                },
                impact="AI 模型对「最强」「最佳」「完美」等绝对化表述的信任度低；若无充分依据，这类内容会被判定为营销稿而降低引用权重。",
                fix="把绝对化表述收窄为「在 X 场景下」「对 Y 需求而言」等具体限定语；或为每个绝对化结论补充可验证的对比维度（如「价格对比」「规格对比」）。",
                layer="editorial",
                page_url=url,
            ))

    # ─── 规则 2：独立第三方证据外链数量（通用，不依赖行业白名单） ─
    links = _v(content.get("links")) or {}
    citation_count = links.get("citation_count", 0)
    citations = links.get("citations", [])

    if citation_count == 0 and word_count > 800:
        issues.append(make_issue(
            category="argumentation",
            kind="no_authority_external_links",
            severity="High",
            title=f"正文 {word_count} 词但无任何指向独立第三方来源/评测的外链",
            evidence={
                "word_count": {"value": word_count, "location": "page"},
                "citation_count": {"value": 0, "location": "external_links"},
                "same_brand_count": {"value": links.get("same_brand_count", 0),
                                     "location": "external_links"},
                "utility_count": {"value": links.get("utility_count", 0),
                                  "location": "external_links"},
            },
            impact="AI 模型判断内容可信度的核心信号是「引用了哪些独立第三方来源」——零独立外链意味着内容类似纯营销稿，AI 倾向选择有独立评测引用的竞品内容。",
            fix="为核心论点补充 3-5 条指向独立第三方来源的链接：行业权威媒体 / 专业评测机构 / 学术标准与认证机构 / 百科资料。每条链接应支撑一个具体事实或测试结论，避免单纯放置无意义链接。",
            layer="editorial",
            page_url=url,
        ))
    elif citation_count < 3 and word_count > 800:
        issues.append(make_issue(
            category="argumentation",
            kind="few_authority_external_links",
            severity="Medium",
            title=f"正文 {word_count} 词仅 {citation_count} 条独立第三方外链，偏少",
            evidence={
                "word_count": {"value": word_count, "location": "page"},
                "citation_count": {"value": citation_count, "location": "external_links"},
                "citations": {"value": [c["url"] for c in citations[:5]],
                              "location": "external_links"},
                "citation_domains": {"value": links.get("citation_domains", []),
                                    "location": "external_links"},
            },
            impact="独立第三方外链偏少，会让 AI 在生成行业盘点与推荐答案时倾向于引用证据更充分的竞品页面。",
            fix="为核心段落补充 2-3 条指向独立外部来源的链接，让 AI 识别出内容具备客观证据支撑。",
            layer="editorial",
            page_url=url,
        ))

    # ─── 规则 3：数字密度低 ──────────────────────────────────────
    numbers = _v(content.get("numbers")) or {}
    num_count = numbers.get("count", 0)

    if word_count > 800 and num_count < 3:
        issues.append(make_issue(
            category="argumentation",
            kind="low_numeric_density",
            severity="Medium",
            title=f"正文 {word_count} 词但具体数字仅 {num_count} 个",
            evidence={
                "word_count": {"value": word_count, "location": "page"},
                "numbers_count": {"value": num_count, "location": "text_content"},
                "examples": {"value": numbers.get("examples", []), "location": "text_content"},
            },
            impact="AI 检索时偏好含具体规格数字的内容（像素/电池/视角/价格）；数字密度低会让 AI 转向内容更具体的竞品页面。",
            fix="为核心段落补 2-3 个具体数字：规格（如 5MP/IP66/2K）/ 价格（如 $116）/ 比较维度（如 40% 更低）。",
            layer="editorial",
            page_url=url,
        ))

    return issues
