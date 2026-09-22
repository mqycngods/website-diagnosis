"""内容信号：正文文本 + 标题 + 段落结构。

为 argumentation / coverage 提供原料。
"""
from __future__ import annotations

import re

from . import make_signal


# 绝对化表述词表（中英文）
ABSOLUTE_TERMS = [
    "最强", "最佳", "最好", "完美", "极致", "绝对", "肯定", "一定", "毫无疑问",
    "best", "strongest", "perfect", "ultimate", "always", "never", "guaranteed",
    "top choice", "no subscription", "the strongest answer", "the best answer",
]

# 数字密度信号（含单位的数值）
NUMBER_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:MP|K|p|GB|TB|mAh|GHz|°|mm|cm|km|h|hours|minutes|%|°C|°F)\b",
    re.I,
)

# 通用非内容型工具域名（跨行业通用黑名单：社交账号、跟踪统计、基础设施、支付等）
# 这些属于站外渠道或技术设施，不属于正文中的「独立第三方证据引用 (Citations)」
UTILITY_DOMAINS = {
    # 社交媒体平台（属于 sameAs/品牌资产，非独立文章证据）
    "facebook.com", "fb.com", "instagram.com", "twitter.com", "x.com",
    "linkedin.com", "youtube.com", "youtu.be", "tiktok.com", "pinterest.com",
    "reddit.com", "weibo.com", "weixin.qq.com",
    # 支付与电商基础设施
    "shopify.com", "myshopify.com", "paypal.com", "stripe.com", "apple.com/apple-pay",
    # 统计、字体、地图与 CDN 基础设施
    "google.com/maps", "google-analytics.com", "googletagmanager.com",
    "cloudflare.com", "akamai.com", "typekit.net", "fonts.googleapis.com",
}

# 高信誉顶级域名 / 国际标准组织特征（跨行业通用结构特征）
HIGH_TRUST_PATTERNS = [
    re.compile(r"\.(gov|edu|ac\.[a-z]{2}|org)$", re.I),
    re.compile(r"(w3\.org|ietf\.org|iso\.org|ieee\.org|wikipedia\.org|wikidata\.org)", re.I),
]


def _count_absolute_terms(text: str) -> list:
    hits = []
    text_lower = text.lower()
    for t in ABSOLUTE_TERMS:
        if t.lower() in text_lower:
            # 找出第一次出现位置（粗略）
            idx = text_lower.find(t.lower())
            hits.append({"term": t, "first_pos": idx})
    return hits


def _count_numbers(text: str) -> dict:
    matches = NUMBER_RE.findall(text)
    return {"count": len(matches), "examples": matches[:10]}


def _classify_links(external_links: list, current_url: str = "") -> dict:
    """通用外链分类（无行业偏见）：

    1. citations: 真正指向独立第三方的外部内容/媒体/评测引用
    2. same_brand_links: 指向本品牌其他站点或子域名的链接
    3. utility_links: 社交主页、支付通道、CDN 等工具型链接
    """
    current_host = current_url.split("//")[-1].split("/")[0].lower() if current_url else ""
    base_domain = ".".join(current_host.split(".")[-2:]) if current_host.count(".") >= 1 else current_host

    citations = []
    same_brand = []
    utility = []

    for link in external_links or []:
        url = link.get("url") if isinstance(link, dict) else str(link)
        anchor_text = link.get("text", "").strip() if isinstance(link, dict) else ""
        if not url:
            continue

        host = url.split("//")[-1].split("/")[0].lower()
        clean_host = re.sub(r"^www\.", "", host)

        # 1. 过滤同品牌跨站/子域链接
        if base_domain and base_domain in clean_host:
            same_brand.append({"url": url, "domain": clean_host, "text": anchor_text})
            continue

        # 2. 过滤通用工具型/社媒链接
        if any(u in clean_host for u in UTILITY_DOMAINS):
            utility.append({"url": url, "domain": clean_host, "text": anchor_text})
            continue

        # 3. 判定为独立第三方引用 (Citations)
        is_high_trust = any(p.search(clean_host) for p in HIGH_TRUST_PATTERNS)
        citations.append({
            "url": url,
            "domain": clean_host,
            "text": anchor_text,
            "is_high_trust_domain": is_high_trust,
        })

    return {
        "citations": citations,
        "citation_count": len(citations),
        "citation_domains": sorted(list({c["domain"] for c in citations})),
        "citation_urls": [c["url"] for c in citations],
        "same_brand_links": same_brand,
        "same_brand_count": len(same_brand),
        "utility_links": utility,
        "utility_count": len(utility),
    }


def gather(page: dict) -> dict:
    """采集内容信号。"""
    if "error" in page:
        return {}
    url = page.get("url", "")
    text = page.get("text_content", "") or ""

    return {
        "url": make_signal(url, "page.url"),
        "text_length": make_signal(len(text), "page.text_content.length"),
        "word_count": make_signal(page.get("word_count") or 0, "page.word_count"),
        "absolute_terms": make_signal(_count_absolute_terms(text), "text_content"),
        "numbers": make_signal(_count_numbers(text), "text_content"),
        "heading_structure": make_signal(
            page.get("heading_structure", []) or [], "HTML headings"),
        "links": make_signal(
            _classify_links(page.get("external_links", []), current_url=url),
            "external_links"),
        "internal_links_count": make_signal(
            len(page.get("internal_links", []) or []), "internal_links"),
        "images": make_signal(page.get("images", []) or [], "HTML <img>"),
    }
