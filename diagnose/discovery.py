"""功能页发现 — 从首页 internal_links + sitemap.xml 识别 Support/FAQ/Contact/About 等页面。

修复的根因：管线此前只做"文章抽样"（sampling.py），从不识别功能页。
报告模板又需要 Homepage/Product/Support/About 这类页面，缺口全甩给 agent，
agent 就硬编码拼 `域名+/support`——撞上 404 再换 `/pages/support` 蒙对。
本模块把"发现功能页"收归确定性脚本：基于真实观测到的链接与 sitemap，
命中就抓，未命中就显式记"未发现"，绝不猜测路径。
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from .sitemap import discover_sitemaps

# 功能页类型 → 命中的关键词（小写匹配 path 与锚文本；含中英文）。
# 只匹配"站点功能/信任"页，不碰产品/文章/集合页（那些由 sampling 管）。
PAGE_TYPE_KEYWORDS = {
    "support": ["support", "help", "帮助", "客服", "售后", "支持中心"],
    "faq": ["faq", "faqs", "question", "常见问题", "疑问"],
    "contact": ["contact", "联系", "联络"],
    "about": ["about", "关于", "品牌", "公司"],
    "warranty": ["warranty", "guarantee", "保修", "质保", "退换"],
}

# 路径里出现这些词，说明是产品/博客/购物车等，不算功能页（防误判）。
_SKIP_PATH = re.compile(
    r"/(products?|collections?|blogs?|news|articles?|press|cart|checkout|"
    r"account|search|wishlist|track|order|cdn)/", re.I)

_SKIP_EXT = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".pdf",
             ".css", ".js", ".xml", ".json", ".zip")


def _norm_path(url: str) -> str:
    return urlparse(url).path.lower().rstrip("/")


def _classify(url: str, anchor_text: str) -> str | None:
    """返回命中的功能页类型；非功能页返回 None。

    判定顺序：功能关键词优先于 _SKIP_PATH。
    Shopify 站常把 FAQ 寄生在 /blogs/faq 这类路径——若 skip 先执行，
    这类真实功能页会被 /blogs/ 规则误杀（实测案例：botslab.com）。
    路径匹配按「段」精确或前缀（faq / about-botslab），锚文本走子串（覆盖中文关键词）。
    """
    path = _norm_path(url)
    if not path or path == "/":
        return None
    if path.endswith(_SKIP_EXT):
        return None
    segments = [s for s in path.split("/") if s]
    anchor = (anchor_text or "").lower()
    for ptype, keywords in PAGE_TYPE_KEYWORDS.items():
        for k in keywords:
            if k in anchor:
                return ptype
            if any(seg == k or seg.startswith(k + "-") or seg.startswith(k + "_")
                   for seg in segments):
                return ptype
    if _SKIP_PATH.search(path + "/"):
        return None
    return None


def discover_functional_pages(internal_links, sitemap_urls=None,
                              per_type_limit: int = 1) -> dict:
    """从首页内部链接（可选 sitemap）里挑出功能页。

    返回 {page_type: url}，每类最多 per_type_limit 个，未命中则不含该键。
    """
    found = {}
    candidates = []
    for link in internal_links or []:
        if isinstance(link, dict):
            candidates.append((link.get("url", ""), link.get("text", "")))
        else:
            candidates.append((str(link), ""))
    for u in sitemap_urls or []:
        candidates.append((u, ""))

    for url, text in candidates:
        if not url or urlparse(url).scheme not in ("http", "https"):
            continue
        ptype = _classify(url, text)
        if ptype and ptype not in found:
            found[ptype] = url
    return found


def fetch_sitemap_urls(base_url: str, timeout: int = 10, limit: int = 200) -> list:
    """尽力解析 sitemap.xml（含 sitemap index），返回 URL 列表。失败返回 []。

    实现已合并到 diagnose/sitemap.py（原与 robots.py 双份解析逻辑合一）。
    """
    return discover_sitemaps(base_url, timeout=timeout, limit=limit)


def audit_functional_pages(url: str, internal_links, fetch_one,
                           per_type_limit: int = 1, timeout: int = 10) -> dict:
    """功能页采集主入口：发现 → 抓取 → 状态标注。

    fetch_one: 抓单页的函数（fetch_mod.fetch_page），便于测试注入。
    返回 {"found": {type: {...}}, "missing": [type...], "sources": {...}}。
    关键纪律：只对真实发现的 URL 抓取；未发现的类型列入 missing，绝不拼路径。
    """
    sitemap_urls = fetch_sitemap_urls(url, timeout=timeout)
    found_map = discover_functional_pages(
        internal_links, sitemap_urls, per_type_limit=per_type_limit)

    all_types = list(PAGE_TYPE_KEYWORDS.keys())
    result = {"found": {}, "missing": [], "sources": {
        "internal_links": len(internal_links or []),
        "sitemap_urls": len(sitemap_urls),
    }}
    for ptype in all_types:
        page_url = found_map.get(ptype)
        if not page_url:
            result["missing"].append(ptype)
            continue
        try:
            fetched = fetch_one(page_url)
        except Exception as e:
            fetched = {"url": page_url, "error": f"抓取失败：{e}"}
        # 抓回 404/软 404 也算"未真正发现"，归入 missing，避免把错误页当诊断对象
        status = fetched.get("status_code") if isinstance(fetched, dict) else None
        if status and status >= 400:
            fetched["note"] = f"发现的链接返回 {status}，按缺失处理"
            result["missing"].append(ptype)
            result["found"][ptype] = fetched  # 保留证据
            continue
        result["found"][ptype] = fetched
    return result
