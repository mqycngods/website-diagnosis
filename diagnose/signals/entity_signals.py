"""实体信号：Organization / Person / Product / Article 的跨页提取。

为 entity_graph 分析器提供原料。
"""
from __future__ import annotations

from . import make_signal


def _find_type(sd_list, type_name):
    """在 structured_data 中找指定 @type 的所有节点。"""
    out = []

    def walk(node, path=""):
        if isinstance(node, dict):
            t = node.get("@type")
            types = t if isinstance(t, list) else [t]
            if type_name in types:
                out.append((node, path))
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")

    for sd in sd_list or []:
        walk(sd, "schema")
    return out


def _gather_page_entities(page: dict) -> dict:
    """单页实体信号（内部用，不带 make_signal 包装）。"""
    sd = page.get("structured_data", []) or []

    orgs = _find_type(sd, "Organization")
    org_summary = None
    if orgs:
        node, _ = orgs[0]
        org_summary = {
            "name": node.get("name"),
            "url": node.get("url"),
            "@id": node.get("@id"),
            "sameAs": node.get("sameAs"),
            "logo": node.get("logo") if isinstance(node.get("logo"), (str, dict)) else None,
            "founder": node.get("founder"),
            "foundingDate": node.get("foundingDate"),
            "address": node.get("address"),
            "contactPoint": node.get("contactPoint"),
        }

    persons = _find_type(sd, "Person")
    person_summaries = []
    for node, path in persons:
        person_summaries.append({
            "name": node.get("name"),
            "@id": node.get("@id"),
            "url": node.get("url"),
            "sameAs": node.get("sameAs"),
            "jobTitle": node.get("jobTitle"),
            "worksFor": node.get("worksFor"),
            "location": path,
        })

    products = _find_type(sd, "Product")
    product_summaries = []
    for node, path in products:
        product_summaries.append({
            "name": node.get("name"),
            "@id": node.get("@id"),
            "sku": node.get("sku"),
            "gtin": node.get("gtin") or node.get("gtin13") or node.get("gtin12"),
            "brand": node.get("brand"),
            "has_review": bool(node.get("review")),
            "has_aggregate_rating": bool(node.get("aggregateRating")),
            "has_offers": bool(node.get("offers")),
            "location": path,
        })

    articles = (_find_type(sd, "Article") +
                _find_type(sd, "BlogPosting") +
                _find_type(sd, "NewsArticle"))
    article_summaries = []
    for node, path in articles:
        article_summaries.append({
            "headline": node.get("headline"),
            "description": node.get("description"),
            "@id": node.get("@id"),
            "datePublished": node.get("datePublished"),
            "dateModified": node.get("dateModified"),
            "author": node.get("author"),
            "publisher": node.get("publisher"),
            "image": node.get("image"),
            "location": path,
        })

    return {
        "organization": org_summary,
        "persons": person_summaries,
        "products": product_summaries,
        "articles": article_summaries,
    }


def gather(page: dict) -> dict:
    """采集实体相关信号。"""
    if "error" in page:
        return {}
    url = page.get("url", "")
    ent = _gather_page_entities(page)

    return {
        "url": make_signal(url, "page.url"),
        "organization": make_signal(ent["organization"], "schema.Organization"),
        "persons": make_signal(ent["persons"], "schema.Person"),
        "products": make_signal(ent["products"], "schema.Product"),
        "articles": make_signal(ent["articles"], "schema.Article"),
    }
