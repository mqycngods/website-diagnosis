"""旧版采集数据的格式迁移 — 让历史 work/*.json 在新版渲染层可用。

render/build.py 不再内嵌兼容逻辑；新增格式变更时在这里加迁移函数。
"""
from __future__ import annotations

_ARTICLE_FAMILY = ("Article", "NewsArticle", "BlogPosting")


def _ok(payload) -> bool:
    return isinstance(payload, dict) and "error" not in payload and bool(payload)


def normalize_schema(schema: dict, fetch: dict) -> dict:
    """旧版 schema.json（BlogPosting/NewsArticle 独立行、Product 标 Missing）→ 三态新格式。

    归并 Article 家族；Product/Service 在未采产品页时由 Missing 改标 NotSampled；
    补 sampled 结构与电商识别。新版 collect 已产出新格式时直接透传。
    """
    if not _ok(schema):
        return schema
    inv = schema.get("inventory") or []
    names = {i.get("schema_type") for i in inv}
    already = any("含" in (n or "") for n in names) or \
        any(i.get("status") == "NotSampled" for i in inv)
    if already:
        schema.setdefault("sampled", {
            "homepage": 1,
            "article_pages": max(0, (schema.get("sampled_pages") or 1) - 1),
            "product_pages": 0,
        })
        return schema

    present_types = {i.get("schema_type") for i in inv if i.get("status") == "Present"}
    article_covered = bool(present_types & set(_ARTICLE_FAMILY))
    product_pages = 0  # 旧数据 collect 时未抓产品页

    out = []
    seen_article = False
    for i in inv:
        t = i.get("schema_type")
        if t in _ARTICLE_FAMILY:
            if seen_article:
                continue  # 家族成员只保留一行
            seen_article = True
            out.append({**i, "schema_type": "Article（含 NewsArticle/BlogPosting）",
                        "status": "Present" if article_covered else "Missing"})
        elif t in ("Product", "Service") and i.get("status") == "Missing" and product_pages == 0:
            out.append({**i, "status": "NotSampled"})
        else:
            out.append(i)

    # 电商识别（内链路径特征），命中则 Product 影响升级
    from diagnose.schema_check import detect_ecommerce
    is_ecom = detect_ecommerce(fetch.get("internal_links", []) if _ok(fetch) else [])
    if is_ecom:
        for i in out:
            if i.get("schema_type") == "Product" and i.get("impact") == "Medium":
                i["impact"] = "High"

    schema["inventory"] = out
    schema["is_ecommerce"] = is_ecom
    schema["sampled"] = {
        "homepage": 1,
        "article_pages": max(0, (schema.get("sampled_pages") or 1) - 1),
        "product_pages": 0,
    }
    return schema
