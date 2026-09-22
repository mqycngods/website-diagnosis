"""权威性分析：外部信号（维基、权威媒体、社交）。

本模块只做「站外信号聚合」的占位——具体抓取在 brand.py 已完成。
这里只补充对「是否有权威外链」「是否有可信社交账号」的统一判断。
"""
from __future__ import annotations

from . import make_issue, register


def _v(sig):
    return (sig or {}).get("value")


@register("authority")
def analyze(signals: dict) -> list:
    issues = []
    main = signals.get("main_page") or {}
    content = main.get("content") or {}
    entity = main.get("entity") or {}
    meta = main.get("meta") or {}
    url = _v(meta.get("url")) or ""

    links = _v(content.get("links")) or {}
    # signals 已把外链结构化为 citations（独立第三方内容外链，剔除社媒与支付噪音）。
    # 权威外链 = citations ∪ 老字段 authority_links（兼容历史数据）。
    citations = links.get("citations") or []
    legacy = links.get("authority_links") or []
    authority_links = list({(c or {}).get("url"): c for c in (citations + legacy) if (c or {}).get("url")}.values())

    # ─── 规则 1：核心品牌页应有指向权威平台的链接 ───────────────
    # 仅对主页/About 页提示
    if url.rstrip("/").count("/") <= 3:  # 主页 / 一级栏目
        if not authority_links:
            issues.append(make_issue(
                category="authority",
                kind="no_authority_links_on_home",
                severity="Low",
                title="主页未链向任何权威外部平台",
                evidence={
                    "url": {"value": url, "location": "page"},
                    "authority_links_count": {"value": 0, "location": "external_links"},
                },
                impact="主页是品牌权威性的核心入口；缺少指向 Wikipedia/权威媒体/官方社交的链接，AI 无法快速确认品牌的站外存在。",
                fix="在主页页脚或 About 区域补 3-5 个权威外链：官方 YouTube/LinkedIn/X/Facebook、Crunchbase、Amazon Brand Store、媒体报道页。",
                layer="domain",
                page_url=url,
            ))

    return issues
