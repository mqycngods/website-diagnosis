"""Meta 信号：title / description / og / twitter / canonical / lang 等。

所有与「页面元数据」相关的事实都从这里采集，供 consistency 与 coverage 使用。
"""
from __future__ import annotations

from . import make_signal


def gather(page: dict) -> dict:
    """从 fetch_page 结果提取元数据信号。"""
    if "error" in page:
        return {}

    url = page.get("url", "")
    meta = page.get("meta_tags", {}) or {}

    out = {
        "url": make_signal(url, "page.url"),
        "title": make_signal(page.get("title") or "", "HTML <title>"),
        "meta_description": make_signal(
            meta.get("description") or "", 'HTML <meta name="description">'),
        "canonical": make_signal(page.get("canonical") or "", 'HTML <link rel="canonical">'),
        "lang": make_signal(
            # fetch.py 当前没显式抓 lang，但可能藏在 html 标签属性里——由 content_signals 补
            page.get("lang") or "", "HTML <html lang>"),
        "og_title": make_signal(meta.get("og:title") or "", 'HTML <meta property="og:title">'),
        "og_description": make_signal(
            meta.get("og:description") or "", 'HTML <meta property="og:description">'),
        "og_url": make_signal(meta.get("og:url") or "", 'HTML <meta property="og:url">'),
        "og_image": make_signal(meta.get("og:image") or "", 'HTML <meta property="og:image">'),
        "og_type": make_signal(meta.get("og:type") or "", 'HTML <meta property="og:type">'),
        "og_site_name": make_signal(
            meta.get("og:site_name") or "", 'HTML <meta property="og:site_name">'),
        "twitter_title": make_signal(
            meta.get("twitter:title") or "", 'HTML <meta name="twitter:title">'),
        "twitter_description": make_signal(
            meta.get("twitter:description") or "", 'HTML <meta name="twitter:description">'),
        "twitter_card": make_signal(
            meta.get("twitter:card") or "", 'HTML <meta name="twitter:card">'),
        "h1_tags": make_signal(page.get("h1_tags") or [], "HTML <h1>"),
        "word_count": make_signal(page.get("word_count") or 0, "page.word_count"),
        "has_ssr_content": make_signal(
            page.get("has_ssr_content"), "page.has_ssr_content"),
        "status_code": make_signal(page.get("status_code"), "page.status_code"),
    }
    return out
