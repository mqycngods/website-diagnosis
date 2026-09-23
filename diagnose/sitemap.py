"""sitemap.xml 解析 — robots.py 与 discovery.py 的共用实现（原双份代码合并于此）。

两个用途：
  parse_sitemap_urls   已知 sitemap URL → 解析出 {url, lastmod} 列表（含 sitemap index）
  discover_sitemaps    站点根 URL → 依次探测常见路径，返回 URL 字符串列表
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from .http_utils import get

DEFAULT_PATHS = ("/sitemap.xml", "/sitemap_index.xml", "/sitemapindex.xml")


def _walk(sm_url: str, out: list, seen: set, timeout: int, limit: int,
          depth: int, with_lastmod: bool):
    if depth > 2 or sm_url in seen or len(out) >= limit:
        return
    seen.add(sm_url)
    try:
        r = get(sm_url, timeout=timeout)
        if r.status_code != 200:
            return
        root_el = ET.fromstring(r.content)
        tag = root_el.tag.lower()
        ns = tag[: tag.index("}") + 1] if tag.startswith("{") else ""
        if "sitemapindex" in tag:
            for sm in root_el.findall(f"{ns}sitemap"):
                loc = sm.find(f"{ns}loc")
                if loc is not None and loc.text:
                    _walk(loc.text.strip(), out, seen, timeout, limit,
                          depth + 1, with_lastmod)
        else:
            for u in root_el.findall(f"{ns}url"):
                loc = u.find(f"{ns}loc")
                if loc is not None and loc.text:
                    if with_lastmod:
                        lastmod_el = u.find(f"{ns}lastmod")
                        out.append({
                            "url": loc.text.strip(),
                            "lastmod": (lastmod_el.text.strip()
                                        if lastmod_el is not None and lastmod_el.text else None),
                        })
                    else:
                        out.append(loc.text.strip())
                    if len(out) >= limit:
                        return
    except Exception:
        return


def parse_sitemap_urls(sm_url: str, timeout: int = 10, limit: int = 500) -> list:
    """解析 sitemap URL 列表（含 sitemap index），返回 [{"url", "lastmod"}...]。"""
    out, seen = [], set()
    _walk(sm_url, out, seen, timeout, limit, 0, with_lastmod=True)
    return out


def discover_sitemaps(base_url: str, timeout: int = 10, limit: int = 200,
                      extra_candidates: list = None) -> list:
    """尽力发现 sitemap 并解析出 URL 字符串列表。

    候选顺序：extra_candidates（如 robots.txt 声明的 sitemap）→ 常见默认路径。
    失败返回 []。
    """
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    candidates = list(extra_candidates or []) + [root + p for p in DEFAULT_PATHS]

    for sm_url in candidates:
        out, seen = [], set()
        _walk(sm_url, out, seen, timeout, limit, 0, with_lastmod=False)
        if out:
            return out
    return []
