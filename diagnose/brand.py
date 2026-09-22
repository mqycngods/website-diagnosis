"""品牌站外实体探测 — AI 模型用于实体识别的第三方权威信号。

Wikipedia / Wikidata 用官方 API 验证（确定性，无误报）；
Reddit / YouTube / LinkedIn 提供搜索入口供 agent 复核（WebFetch 充实）。
权重：Wiki 30 · Reddit 20 · YouTube 15 · LinkedIn 10 · 行业源 25。
"""
from __future__ import annotations

import json
from urllib.parse import quote, urlparse

from .http_utils import get

# 各平台权重（满分 100）
WEIGHTS = {"wikipedia": 30, "reddit": 20, "youtube": 15, "linkedin": 10, "industry": 25}


def _brand_from_url(url: str) -> str:
    host = urlparse(url).netloc.replace("www.", "")
    return host.split(".")[0]


def check_wikipedia(brand: str, timeout: int = 10) -> dict:
    """Wikipedia + Wikidata API 验证（确定性）。"""
    out = {"has_wikipedia_page": False, "has_wikidata_entry": False,
           "wikipedia_title": None, "wikidata_id": None, "wikidata_description": None}
    try:
        # Wikipedia 搜索
        r = get(f"https://www.wikidata.org/w/api.php?action=wbsearchentities"
                f"&search={quote(brand)}&language=en&format=json&limit=3", timeout=timeout)
        data = r.json()
        results = data.get("search", [])
        if results:
            top = results[0]
            # 品牌名与条目 label 匹配（不区分大小写、忽略后缀词）
            label = (top.get("label") or "").lower()
            if brand.lower() in label or label in brand.lower():
                out["has_wikidata_entry"] = True
                out["wikidata_id"] = top.get("id")
                out["wikidata_description"] = top.get("description")
        # Wikipedia 词条
        r2 = get(f"https://en.wikipedia.org/w/api.php?action=query&list=search"
                 f"&srsearch={quote(brand)}&format=json&srlimit=3", timeout=timeout)
        hits = r2.json().get("query", {}).get("search", [])
        if hits:
            title = hits[0].get("title", "")
            if brand.lower() in title.lower():
                out["has_wikipedia_page"] = True
                out["wikipedia_title"] = title
    except Exception:
        pass
    return out


def scan_brand(brand: str = None, url: str = None, timeout: int = 10) -> dict:
    """站外实体扫描。brand 缺省时从 url 推断。"""
    if not brand and url:
        brand = _brand_from_url(url)
    if not brand:
        return {"error": "需要 brand 或 url"}

    wiki = check_wikipedia(brand, timeout)

    # Reddit / YouTube / LinkedIn：构造搜索入口，agent 用 WebFetch 充实
    platforms = {
        "wikipedia": {
            "weight": WEIGHTS["wikipedia"],
            "present": wiki["has_wikipedia_page"] or wiki["has_wikidata_entry"],
            "source": "API",
            "detail": f"Wikidata: {wiki['wikidata_id'] or '无'} · Wikipedia: {wiki['wikipedia_title'] or '无'}",
            "earned": WEIGHTS["wikipedia"] if (wiki["has_wikipedia_page"] or wiki["has_wikidata_entry"]) else 0,
        },
        "reddit": {
            "weight": WEIGHTS["reddit"], "present": None, "source": "",
            "search_url": f"https://www.reddit.com/search/?q={quote(brand)}",
            "detail": "待 agent 复核",
            "earned": 0,
        },
        "youtube": {
            "weight": WEIGHTS["youtube"], "present": None, "source": "",
            "search_url": f"https://www.youtube.com/results?search_query={quote(brand)}",
            "detail": "待 agent 复核",
            "earned": 0,
        },
        "linkedin": {
            "weight": WEIGHTS["linkedin"], "present": None, "source": "",
            "search_url": f"https://www.linkedin.com/search/results/companies/?keywords={quote(brand)}",
            "detail": "待 agent 复核",
            "earned": 0,
        },
        "industry": {
            "weight": WEIGHTS["industry"], "present": None, "source": "",
            "search_url": f"https://crunchbase.com/search/organizations/field/organizations/name/{quote(brand)}",
            "detail": "待 agent 复核（Crunchbase/G2/Trustpilot 任一确认即得分）",
            "earned": 0,
        },
    }

    # 基线分 = 仅 API 已验证部分（Wiki）。agent 复核其它平台后可上调。
    baseline = sum(p["earned"] for p in platforms.values())

    return {
        "brand_name": brand, "url": url,
        "wikipedia": wiki,
        "platforms": platforms,
        "baseline_score": baseline,
        "note": "baseline_score 仅含 Wikipedia/Wikidata（API 已验证）；Reddit/YouTube/LinkedIn/行业源需 agent 用 WebFetch 复核后上调。",
    }
