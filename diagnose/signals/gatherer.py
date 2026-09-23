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


def _aggregate_entities_all_pages(main_page: dict, articles: dict,
                                  functional_pages: dict = None) -> dict:
    """跨页实体聚合：把所有页面的 Organization/Person/Product/Article 集中到一个池子。

    返回 {page_url, entity_fields...} 列表，供 entity_graph 做跨页面对比。
    功能页纳入聚合（About 页常带 Organization），但不进作者/内容类规则的判定范围。
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
    for p in (functional_pages or {}).values():
        if isinstance(p, dict):
            add(p)
    return out


def _functional_summary(functional_pages: dict) -> dict:
    """功能页站点级摘要：{page_type: {url, status, word_count, h1, has_faq_schema}}。

    供 FAQ 判定树（GEO-COV-008）等站点级规则使用；含「发现的链接返回 4xx」的
    降级页（status >= 400 时 status 原样保留，由规则决定如何处理）。
    """
    def _has_faq(node):
        if isinstance(node, dict):
            t = node.get("@type")
            ts = t if isinstance(t, list) else [t]
            if "FAQPage" in ts:
                return True
            return any(_has_faq(v) for v in node.values())
        if isinstance(node, list):
            return any(_has_faq(i) for i in node)
        return False

    out = {}
    for ptype, pg in (functional_pages or {}).items():
        if not isinstance(pg, dict):
            continue
        sd = pg.get("structured_data") or []
        out[ptype] = {
            "url": pg.get("url"),
            "status": pg.get("status_code"),
            "word_count": pg.get("word_count") or 0,
            "h1": (pg.get("h1_tags") or [None])[0],
            "has_faq_schema": _has_faq(sd),
            "note": pg.get("note", ""),
        }
    return out


def _has_faqpage_schema(page: dict) -> bool:
    sd = (page or {}).get("structured_data") or []
    def walk(node):
        if isinstance(node, dict):
            t = node.get("@type")
            ts = t if isinstance(t, list) else [t]
            if "FAQPage" in ts:
                return True
            return any(walk(v) for v in node.values())
        if isinstance(node, list):
            return any(walk(i) for i in node)
        return False
    return walk(sd)


def gather_all(main_page: dict, articles: dict, functional_pages: dict = None) -> dict:
    """对整个诊断会话采集：主页 + 所有文章页 + 所有产品页 + 功能页。

    functional_pages: pages.json 的 found 字典 {page_type: fetch_page 结果}。
    功能页进 signals.functional_pages（带 page_type）与 global 实体聚合；
    内容型规则（argumentation/consistency 作者类）不应消费它们。

    返回:
      {
        "main_page": {...signals...},
        "article_pages": [ {...}, ... ],
        "product_pages": [ {...}, ... ],
        "functional_pages": [ {...page_type...}, ... ],
        "site": {"functional": {page_type: 摘要}},
        "global": {
          "entities": {organizations, persons, products, articles},  # 跨页聚合
        }
      }
    """
    content_pages = [main_page] + (articles.get("pages", []) or []) + \
                    (articles.get("product_pages", []) or [])
    out = {
        "main_page": gather_page(main_page) if "error" not in main_page else {},
        "article_pages": [],
        "product_pages": [],
        "functional_pages": [],
        "site": {
            "functional": _functional_summary(functional_pages),
            # FAQPage 是否已在内容页检出（影响 GEO-COV-008 的定级）
            "faqpage_on_content_pages": any(
                _has_faqpage_schema(p) for p in content_pages if isinstance(p, dict)),
        },
        "global": {
            "entities": _aggregate_entities_all_pages(main_page, articles, functional_pages),
        },
    }
    for p in articles.get("pages", []) or []:
        if "error" not in p:
            out["article_pages"].append(gather_page(p))
    for p in articles.get("product_pages", []) or []:
        if "error" not in p:
            out["product_pages"].append(gather_page(p))
    for ptype, p in (functional_pages or {}).items():
        if isinstance(p, dict) and "error" not in p:
            sig = gather_page(p)
            sig["page_type"] = ptype
            out["functional_pages"].append(sig)
    return out
