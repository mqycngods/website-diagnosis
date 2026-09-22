"""gather_all: 对单个页面跑所有信号采集器，输出统一 signals 结构。"""
from __future__ import annotations

from . import author_signals, content_signals, entity_signals, meta_signals


def gather_page(page: dict) -> dict:
    """对单个 fetch_page 结果跑所有采集器，输出按类别分组的信号。"""
    return {
        "url": page.get("url"),
        "meta": meta_signals.gather(page),
        "author": author_signals.gather(page),
        "entity": entity_signals.gather(page),
        "content": content_signals.gather(page),
    }


def _aggregate_entities_all_pages(main_page: dict, articles: dict) -> dict:
    """跨页实体聚合：把所有页面的 Organization/Person/Product/Article 集中到一个池子。

    返回 {page_url, entity_fields...} 列表，供 entity_graph 做跨页面对比。
    """
    out = {"organizations": [], "persons": [], "products": [], "articles": []}

    def add(page_dict):
        if "error" in page_dict:
            return
        url = page_dict.get("url", "")
        ent = entity_signals._gather_page_entities(page_dict)
        if ent["organization"]:
            out["organizations"].append({"page_url": url, **ent["organization"]})
        for p in ent["persons"]:
            out["persons"].append({"page_url": url, **p})
        for p in ent["products"]:
            out["products"].append({"page_url": url, **p})
        for a in ent["articles"]:
            out["articles"].append({"page_url": url, **a})

    add(main_page)
    for p in articles.get("pages", []) or []:
        add(p)
    for p in articles.get("product_pages", []) or []:
        add(p)
    return out


def gather_all(main_page: dict, articles: dict) -> dict:
    """对整个诊断会话采集：主页 + 所有文章页 + 所有产品页。

    返回:
      {
        "main_page": {...signals...},
        "article_pages": [ {...}, ... ],
        "product_pages": [ {...}, ... ],
        "global": {
          "entities": {organizations, persons, products, articles},  # 跨页聚合
        }
      }
    """
    out = {
        "main_page": gather_page(main_page) if "error" not in main_page else {},
        "article_pages": [],
        "product_pages": [],
        "global": {
            "entities": _aggregate_entities_all_pages(main_page, articles),
        },
    }
    for p in articles.get("pages", []) or []:
        if "error" not in p:
            out["article_pages"].append(gather_page(p))
    for p in articles.get("product_pages", []) or []:
        if "error" not in p:
            out["product_pages"].append(gather_page(p))
    return out
