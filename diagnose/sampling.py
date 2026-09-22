"""文章页抽样 — 新鲜度 / Article schema 是文章级信号，首页通常不带。

首页的 internal_links 里筛出"像文章"的 URL（/blog/、/news/、日期路径等），
均匀取样（不取前 N 个，避免偏向最新推荐位），供 collect 逐页抓取。
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

ARTICLE_PATH = re.compile(
    r"/(blogs?|news|articles?|press|insights?|guides?|resources?|posts?)/[^/]+"
    r"|/\d{4}/\d{2}/", re.I)

# 产品页路径：Shopify /products/x · WooCommerce /product/x · 通用 /item|shop|store/x
PRODUCT_PATH = re.compile(
    r"/(products?|items?|shop|store|goods)/[^/]+", re.I)

SKIP_EXT = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".pdf",
            ".css", ".js", ".xml", ".json")


def _pick_by_pattern(internal_links, pattern, list_page_names, limit):
    """按路径模式从内链抽样：去重、排除栏目列表页本身、均匀取样。"""
    seen, candidates = set(), []
    for link in internal_links or []:
        url = link.get("url") if isinstance(link, dict) else str(link)
        if not url:
            continue
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            continue
        path = p.path.lower()
        if path.endswith(SKIP_EXT) or not pattern.search(path):
            continue
        # 排除栏目列表页本身：/blogs/blog、/products 这类"段落名 == 栏目名"的路径
        segs = [s for s in path.split("/") if s]
        if len(segs) >= 2 and segs[-1] in list_page_names:
            continue
        clean = f"{p.scheme}://{p.netloc}{p.path}".rstrip("/")
        if clean in seen:
            continue
        seen.add(clean)
        candidates.append(clean)
    if len(candidates) <= limit:
        return candidates
    # 均匀取样：首尾都保留，避免只拿到最新或最旧
    step = (len(candidates) - 1) / (limit - 1)
    return [candidates[round(i * step)] for i in range(limit)]


def pick_article_urls(internal_links, limit: int = 5) -> list:
    return _pick_by_pattern(
        internal_links, ARTICLE_PATH,
        ("blog", "blogs", "news", "articles", "press", "insights", "guides", "posts"),
        limit)


def pick_product_urls(internal_links, limit: int = 3) -> list:
    """产品页抽样 — Product schema 是产品级信号，首页/文章页通常不带。

    与文章页同源（首页 internal_links）、同均匀取样策略；首页无产品链接时
    返回空列表，collect 自动跳过，不产生额外请求。
    """
    return _pick_by_pattern(
        internal_links, PRODUCT_PATH,
        ("product", "products", "item", "items", "shop", "store", "goods",
         "collections", "collection", "category", "categories"),
        limit)
