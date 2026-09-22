"""Schema / JSON-LD 检测：清单、关键类型覆盖、sameAs。

状态四态：Present（采样页中检出且合法）/ Broken（存在但 JSON 损坏，不可解析）/
Missing（采样范围内应出现但未标注）/ NotSampled（该类型典型宿主页不在采样范围，未知不断言）。
Article / NewsArticle / BlogPosting 为家族关系——任一检出即文章级覆盖，
合并一行展示，避免对 Shopify 等用父类型标注的站点误报缺失。
"""
from __future__ import annotations

import re

from .fetch import extract_sameas

# 文章级类型家族：schema.org 中 NewsArticle/BlogPosting 是 Article 的子类型，
# 用父类型标注是有效实现（Shopify 默认如此）
ARTICLE_FAMILY = ("Article", "NewsArticle", "BlogPosting")

# 关键类型 → (典型宿主页类型, 基础影响权重)
# 宿主页类型：anywhere（任何页面都可能出现）/ article（文章页）/ product（产品页）
GEO_CRITICAL = {
    "Organization":  ("anywhere", "Critical"),
    "Person":        ("anywhere", "Critical"),
    "Article":       ("article",  "Critical"),   # 含 NewsArticle/BlogPosting
    "WebSite":       ("anywhere", "Medium"),
    "BreadcrumbList": ("anywhere", "Medium"),
    "FAQPage":       ("anywhere", "Medium"),
    "LocalBusiness": ("anywhere", "High"),
    "Product":       ("product",  "Medium"),     # 电商场景升至 High
    "Service":       ("product",  "Medium"),
}

# 电商站特征：Product schema 升级为核心实体
ECOMMERCE_PATH_HINT = re.compile(r"/(products?|collections?)/", re.I)
ECOMMERCE_HTML_HINT = ("cdn.shopify.com", "Shopify.theme", "woocommerce")


def _iter_types(node, acc):
    if isinstance(node, dict):
        t = node.get("@type")
        for v in (t if isinstance(t, list) else [t]):
            if isinstance(v, str):
                acc.add(v.rsplit("/", 1)[-1])
        for v in node.values():
            if isinstance(v, (dict, list)):
                _iter_types(v, acc)
    elif isinstance(node, list):
        for item in node:
            _iter_types(item, acc)


def _collect_broken(structured_data) -> dict:
    """从 structured_data 收集所有损坏的 JSON-LD 块（带 __parse_error 占位）。

    返回 {schema_type: {"error": ..., "raw_preview": ..., "page_url": ...}} 字典。
    家族类型（Article/NewsArticle/BlogPosting）任一损坏即整个家族损坏。
    """
    broken = {}
    for sd in structured_data or []:
        if not isinstance(sd, dict):
            continue
        if sd.get("__parse_error"):
            t = sd.get("@type", "Unknown")
            broken[t] = {
                "error": sd.get("__error", ""),
                "raw_preview": sd.get("__raw_preview", "")[:300],
                "page_url": sd.get("__page_url", ""),
            }
    return broken


def _collect_autofixed(fetch_errors) -> dict:
    """从 fetch_errors 收集「自动修复成功」的 JSON-LD——这些不算 Broken，
    但应作为字段级质量问题（warning）回报给客户。"""
    autofixed = {}
    for err in (fetch_errors or []):
        if not isinstance(err, dict):
            continue
        if err.get("kind") in ("jsonld_html_entities", "jsonld_unescaped_newline", "jsonld_comments") \
                and err.get("auto_fixed"):
            t = err.get("schema_type")
            if t and t != "Unknown":
                autofixed.setdefault(t, []).append({
                    "kind": err.get("kind"),
                    "msg": err.get("msg", ""),
                    "page_url": err.get("page_url", ""),
                    "raw_preview": err.get("raw_preview", "")[:200],
                })
    return autofixed


def detect_ecommerce(internal_links, html_hint: str = "") -> bool:
    """从内链路径或 HTML 特征识别电商站（Shopify / WooCommerce / 通用产品目录）。"""
    for link in internal_links or []:
        url = link.get("url") if isinstance(link, dict) else str(link)
        if url and ECOMMERCE_PATH_HINT.search(str(url)):
            return True
    return any(h in (html_hint or "") for h in ECOMMERCE_HTML_HINT)


def check_schema(structured_data, sampled: dict = None, is_ecommerce: bool = False,
                 fetch_errors: list = None) -> dict:
    """输入合并后的 structured_data，输出 schema 清单 + sameAs。

    sampled: {"homepage": int, "article_pages": int, "product_pages": int}
             实际采样构成；缺省按旧行为（首页 + 文章页）处理。
    fetch_errors: fetch.py 采集到的错误列表（含 invalid_jsonld / jsonld_html_entities 等），
                  用于补充 schema 质量信号。
    """
    sampled = dict(sampled or {})
    sampled.setdefault("homepage", 1)
    sampled.setdefault("article_pages", max(0, (sampled.get("total") or 1) - 1))
    sampled.setdefault("product_pages", 0)

    types = set()
    _iter_types(structured_data or [], types)

    # 收集损坏的 JSON-LD（含 __parse_error 占位）
    broken = _collect_broken(structured_data)
    # 从 fetch_errors 补充损坏类型——只补 auto_fixed=False（真的修不好）的；
    # auto_fixed=True 的脚本已自动修复成功，应进入 Present 并附 warning。
    for err in (fetch_errors or []):
        if isinstance(err, dict) and err.get("kind") == "invalid_jsonld" \
                and not err.get("auto_fixed"):
            t = err.get("schema_type")
            if t and t != "Unknown" and t not in broken:
                broken[t] = {"error": err.get("msg") or err.get("error", ""),
                             "raw_preview": err.get("raw_preview", ""),
                             "page_url": err.get("page_url", "")}

    # 自动修复成功的警告（影响字段质量但不影响类型检出）
    autofixed = _collect_autofixed(fetch_errors)

    article_covered = bool(types & set(ARTICLE_FAMILY))
    article_broken = bool(set(broken.keys()) & set(ARTICLE_FAMILY))
    product_pages = sampled.get("product_pages", 0)

    inventory = []
    for t in sorted(GEO_CRITICAL):
        host, impact = GEO_CRITICAL[t]
        # 优先判定 Broken（存在但损坏）
        if t == "Article":
            is_broken = article_broken
        else:
            is_broken = t in broken
        if is_broken:
            note_detail = broken.get(t) or next(
                (broken[k] for k in ARTICLE_FAMILY if k in broken), {})
            inventory.append({
                "schema_type": "Article（含 NewsArticle/BlogPosting）" if t == "Article" else t,
                "status": "Broken",
                "impact": "Critical",  # 损坏比缺失更严重——可能触发 Google 惩罚
                "error": note_detail.get("error", ""),
                "raw_preview": note_detail.get("raw_preview", ""),
            })
            continue

        if t == "Article":
            present = article_covered
        else:
            present = t in types
        if present:
            status = "Present"
        elif host == "product" and product_pages == 0:
            status = "NotSampled"   # 产品页未抓取，未知不断言
        elif host == "article" and sampled.get("article_pages", 0) == 0:
            status = "NotSampled"
        else:
            status = "Missing"
        if t == "Product" and is_ecommerce and impact == "Medium":
            impact = "High"         # 电商站 Product 是 AI 购物推荐的核心实体
        inventory.append({
            "schema_type": "Article（含 NewsArticle/BlogPosting）" if t == "Article" else t,
            "status": status,
            "impact": impact,
        })
    # 覆盖到但不在关键清单里的类型（家族成员单独出现的也归并进 Article 行，不再单列）
    for t in sorted(types - set(GEO_CRITICAL) - set(ARTICLE_FAMILY)):
        inventory.append({"schema_type": t, "status": "Present", "impact": "Low"})
    # 损坏但不在关键清单里的类型也要列出（避免漏报）
    for t in sorted(set(broken.keys()) - set(GEO_CRITICAL) - set(ARTICLE_FAMILY)):
        detail = broken[t]
        inventory.append({
            "schema_type": t,
            "status": "Broken",
            "impact": "Medium",
            "error": detail.get("error", ""),
            "raw_preview": detail.get("raw_preview", ""),
        })

    # 收集 JSON-LD 字段级质量问题（作为附加信号）
    field_issues = []
    for err in (fetch_errors or []):
        if isinstance(err, dict) and err.get("kind") in (
                "jsonld_html_entities_in_field", "jsonld_html_tags_in_field",
                "jsonld_description_too_long", "jsonld_missing_field"):
            field_issues.append(err)

    sameas = extract_sameas(structured_data)
    valid_blocks = [s for s in (structured_data or []) if not (isinstance(s, dict) and s.get("__parse_error"))]
    broken_blocks = [s for s in (structured_data or []) if isinstance(s, dict) and s.get("__parse_error")]
    return {
        "total_blocks": len(structured_data or []),
        "valid_blocks_count": len(valid_blocks),
        "broken_blocks_count": len(broken_blocks),
        "types_found": sorted(types),
        "inventory": inventory,
        "sameas_links": sameas,
        "sameas_count": len(sameas),
        "has_organization": "Organization" in types or "LocalBusiness" in types,
        "has_person": "Person" in types,
        "has_article": article_covered,
        "broken_count": sum(1 for it in inventory if it.get("status") == "Broken"),
        "autofixed_count": sum(len(v) for v in autofixed.values()),
        "autofixed": autofixed,
        "field_issues": field_issues,
        "sampled": sampled,
        "is_ecommerce": is_ecommerce,
    }
