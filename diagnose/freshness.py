"""内容新鲜度 — dateModified / <time datetime> 提取 + 分级。

约半数被 AI 引用的页面在 13 周（90 天）内有更新，所以 90 天是关键边界。
分级：fresh<90d · aging 90-365d · stale 365-730d · very-stale >730d ·
future-dated（未来日期，标记缺陷）· unknown（无机器可读日期，本身是发现）。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .config import cfg

_tiers_cfg = cfg("freshness.tiers", [90, 365, 730])
TIERS = ((int(_tiers_cfg[0]), "fresh"), (int(_tiers_cfg[1]), "aging"),
         (int(_tiers_cfg[2]), "stale"))


def _parse_date(raw: str):
    if not raw:
        return None
    raw = str(raw).strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        m = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", raw)
        if not m:
            return None
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def extract_freshness(structured_data, soup, headers) -> dict:
    """从 JSON-LD / <time> / Last-Modified 头提取最佳日期与分级。

    优先级：
    1. Article 类实体（Article / NewsArticle / BlogPosting / TechArticle 等）中的 dateModified / datePublished
    2. 其他具体实体（Product, Event, Review 等）中的日期
    3. 页面/站点级实体（WebPage / CollectionPage / WebSite）中的 dateModified / datePublished
    4. HTML <time datetime="..."> 标签
    5. HTTP 响应头 Last-Modified
    """
    result = {"date_published": None, "date_modified": None, "best_date": None,
              "age_days": None, "tier": "unknown", "source": None}

    # 收集全部带日期的实体节点
    article_nodes = []
    other_nodes = []
    page_nodes = []

    def walk(node):
        if isinstance(node, dict):
            tp = node.get("@type")
            types = tp if isinstance(tp, list) else [tp]
            dp = node.get("datePublished")
            dm = node.get("dateModified")
            if dp or dm:
                is_art = any(t in ("Article", "NewsArticle", "BlogPosting", "TechArticle", "ScholarlyArticle") for t in types if isinstance(t, str))
                is_pg = any(t in ("WebPage", "CollectionPage", "WebSite", "ItemPage") for t in types if isinstance(t, str))
                if is_art:
                    article_nodes.append(node)
                elif is_pg:
                    page_nodes.append(node)
                else:
                    other_nodes.append(node)
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(node, list):
            for it in node:
                walk(it)

    walk(structured_data or [])

    # 按优先级尝试提取
    preferred_node = None
    if article_nodes:
        preferred_node = article_nodes[0]
    elif other_nodes:
        preferred_node = other_nodes[0]
    elif page_nodes:
        preferred_node = page_nodes[0]

    if preferred_node:
        result["date_published"] = str(preferred_node.get("datePublished")) if preferred_node.get("datePublished") else None
        result["date_modified"] = str(preferred_node.get("dateModified")) if preferred_node.get("dateModified") else None

    candidates = []
    if result["date_modified"]:
        candidates.append((result["date_modified"], "structured_data"))
    if result["date_published"]:
        candidates.append((result["date_published"], "structured_data"))

    if soup is not None:
        t = soup.find("time", attrs={"datetime": True})
        if t:
            candidates.append((t["datetime"], "time_tag"))
    if headers:
        candidates.append((headers.get("Last-Modified"), "http_header"))

    for raw, source in candidates:
        dt = _parse_date(raw)
        if not dt:
            continue
        age = (datetime.now(timezone.utc) - dt).days
        result.update({"best_date": str(raw), "age_days": age, "source": source})
        if age < -1:
            result["tier"] = "future-dated"
        else:
            result["tier"] = next((t for limit, t in TIERS if age < limit), "very-stale")
        break
    return result


def freshness_tier_zh(tier: str) -> str:
    return {"fresh": "新鲜", "aging": "渐旧", "stale": "陈旧", "very-stale": "严重过时",
            "future-dated": "日期异常", "unknown": "无日期"}.get(tier, tier)
