"""覆盖完备性分析：页面级必备元素是否齐全。

通用规则：
- 列表页（H1 为 blog/news/products 等通用词）应有更语义的标题与描述
- 文章页 Article.description 不应为空
- 所有页面应有非空 meta_description
- H1 不应为空、不应过于通用
- 每页都应有 canonical / og:title / og:image
"""
from __future__ import annotations

import re

from . import make_issue, register


GENERIC_H1 = {"blog", "news", "products", "articles", "home", "index", "welcome",
              "博客", "新闻", "产品", "文章", "首页"}


def _v(sig):
    return (sig or {}).get("value")


def _is_generic_h1(h1: str) -> bool:
    if not h1:
        return True
    n = h1.strip().lower()
    if n in GENERIC_H1:
        return True
    n_clean = re.sub(r"[|\-–—·].*$", "", n).strip()
    return n_clean in GENERIC_H1


@register("coverage")
def analyze(signals: dict) -> list:
    """分析覆盖完备性。同 kind 的多页问题自动聚合为一条，affected_pages 列出所有页面。"""
    raw_issues = []
    main = signals.get("main_page") or {}

    # 所有页面：主页 + 文章页 + 产品页
    all_pages = [main] + (signals.get("article_pages") or []) + \
                (signals.get("product_pages") or [])

    for pg in all_pages:
        if not pg:
            continue
        meta = pg.get("meta") or {}
        entity = pg.get("entity") or {}
        url = _v(meta.get("url")) or ""

        h1_list = _v(meta.get("h1_tags")) or []
        h1 = h1_list[0] if h1_list else ""

        # 规则 1：H1 通用性
        if h1 and _is_generic_h1(h1):
            raw_issues.append(make_issue(
                category="coverage", kind="h1_too_generic", severity="High",
                title=f"H1 过于通用：「{h1}」",
                evidence={
                    "h1": {"value": h1, "location": "HTML <h1>"},
                    "url": {"value": url, "location": "page"},
                },
                impact="H1 是搜索引擎与 AI 识别页面主题的核心信号；过于通用的 H1（如 blog/news）无法让 AI 判断页面是关于什么的。",
                fix="把 H1 改为语义化的栏目名称，例如「Botslab Blog - AI Vision Security Insights」，而非单纯「blog」。",
                layer="template", page_url=url,
            ))

        # 规则 2：meta_description 缺失
        if not _v(meta.get("meta_description")):
            raw_issues.append(make_issue(
                category="coverage", kind="meta_description_missing", severity="Medium",
                title="页面缺 meta description",
                evidence={
                    "url": {"value": url, "location": "page"},
                    "title": {"value": _v(meta.get("title")), "location": "<title>"},
                },
                impact="SERP 摘要不可控，AI 检索时无法拿到页面级描述，影响点击率与引用率。",
                fix="为该页面撰写 155 字符内的 meta description，概括页面核心价值。",
                layer="template", page_url=url,
            ))

        # 规则 3：Article.description 空
        for art in (_v(entity.get("articles")) or []):
            if not art.get("description"):
                raw_issues.append(make_issue(
                    category="coverage", kind="article_description_empty", severity="Medium",
                    title="Article schema 的 description 字段为空",
                    evidence={
                        "article_headline": {"value": (art.get("headline") or "")[:60],
                                             "location": art.get("location")},
                        "meta_description": {"value": (_v(meta.get("meta_description")) or "")[:100],
                                             "location": '<meta name="description">'},
                    },
                    impact="Google AIO / Perplexity 在引用文章时优先读 Article.description；为空则回退到正文随机抽取，摘要质量不可控。",
                    fix="为 Article.description 填入与正文一致的简短摘要；可复用经过编辑确认的 meta description。",
                    layer="template", page_url=url,
                ))

        # 规则 4：og:title / og:image 缺失
        if not _v(meta.get("og_title")):
            raw_issues.append(make_issue(
                category="coverage", kind="og_title_missing", severity="Low",
                title="缺 og:title",
                evidence={"url": {"value": url, "location": "page"}},
                impact="社交平台分享时无法显示标题，link 卡片失效。",
                fix='补 <meta property="og:title">。',
                layer="template", page_url=url,
            ))
        if not _v(meta.get("og_image")):
            raw_issues.append(make_issue(
                category="coverage", kind="og_image_missing", severity="Low",
                title="缺 og:image",
                evidence={"url": {"value": url, "location": "page"}},
                impact="社交平台分享无预览图，点击率下降。",
                fix="补 1200x630 的 og:image。",
                layer="template", page_url=url,
            ))

        # 规则 5：canonical 缺失
        if not _v(meta.get("canonical")):
            raw_issues.append(make_issue(
                category="coverage", kind="canonical_missing", severity="Medium",
                title="页面缺 canonical 链接",
                evidence={"url": {"value": url, "location": "page"}},
                impact="搜索引擎无法判断主页面，可能导致权重分散（特别是带 UTM 参数的 URL）。",
                fix='在 <head> 中加 <link rel="canonical" href="...">。',
                layer="template", page_url=url,
            ))

    return _aggregate_by_kind(raw_issues)


def _aggregate_by_kind(issues: list) -> list:
    """把同 kind + 同 severity + 同 title 的多页 issue 聚合为一条。

    - 单页问题：保持原样
    - 多页问题：合并 affected_pages 列表，title 加「(N 个页面)」，
      fix 前加「模板级问题，改一处修复全部」前缀
    """
    groups = {}
    for it in issues:
        key = (it.get("kind"), it.get("severity"), it.get("title"))
        groups.setdefault(key, []).append(it)

    out = []
    for key, items in groups.items():
        if len(items) == 1:
            # 单页问题：保持原样，但补 affected_pages 便于统一渲染
            item = items[0]
            item["affected_pages"] = [item.get("page_url")] if item.get("page_url") else []
            item["page_count"] = 1
            item["is_template_level"] = False
            out.append(item)
            continue

        # 多页问题：聚合
        first = items[0]
        pages = [it.get("page_url") for it in items if it.get("page_url")]
        pages = list(dict.fromkeys(pages))  # 保序去重

        # 合并 evidence：保留首个，但把 pages 列表加进去
        merged_evidence = dict(first.get("evidence") or {})
        merged_evidence["affected_pages"] = {
            "value": pages[:10],  # 最多展示 10 条
            "location": "multiple pages",
        }

        # 标题加计数
        base_title = first.get("title", "")
        new_title = f"{base_title}（{len(pages)} 个页面）"

        # fix 前加「模板级问题」提示
        base_fix = first.get("fix", "")
        new_fix = (f"【模板级问题】改一处即可修复全部 {len(pages)} 个页面。"
                   f"涉及页面：{', '.join(p.replace('https://www.botslab.com','') for p in pages[:5])}"
                   f"{' 等' if len(pages) > 5 else ''}。"
                   f"\n\n{base_fix}")

        aggregated = dict(first)
        aggregated.update({
            "title": new_title,
            "fix": new_fix,
            "evidence": merged_evidence,
            "affected_pages": pages,
            "page_count": len(pages),
            "is_template_level": True,
            "page_url": pages[0] if pages else "",  # 主展示页面取第一个
            # 影响升级：N 个页面都有这个问题，影响面更大
            "impact": first.get("impact", "") + f"（此问题在 {len(pages)} 个页面同时存在，说明是模板级 bug 而非单点问题）",
        })
        out.append(aggregated)

    return out
