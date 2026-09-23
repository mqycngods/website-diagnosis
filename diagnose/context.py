"""SiteContext 组装 — 跨页派生站点级事实（渲染层不做诊断推断）。"""
from __future__ import annotations


def derive_site_freshness(fetch: dict, articles: dict) -> dict:
    """新鲜度是文章级信号：首页无日期时，用最新鲜的抽样文章页代替，并标明来源。

    输入 fetch.json 与 articles.json，返回处理后的 fetch（原地修改并返回）。
    """
    def ok(d):
        return isinstance(d, dict) and "error" not in d and bool(d)

    if not ok(fetch):
        return fetch
    freshness = fetch.get("freshness")
    if not isinstance(freshness, dict) or freshness.get("tier") != "unknown":
        return fetch

    best = None
    for ap in (articles.get("pages", []) if ok(articles) else []):
        af = ap.get("freshness") or {}
        if af.get("age_days") is not None and (best is None or af["age_days"] < best[0]["age_days"]):
            best = (af, ap.get("url"))
    if best:
        merged = dict(best[0])
        merged["sampled_from"] = best[1]
        merged["homepage_tier"] = "unknown"
        fetch["freshness"] = merged
    return fetch
