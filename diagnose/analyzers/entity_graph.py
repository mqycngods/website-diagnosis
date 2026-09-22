"""实体图谱分析：Organization / Person / Product / Article 是否形成完整实体图。

通用规则：
- 主页必须有 Organization（品牌门面的官方声明）
- Organization 必须有 sameAs / @id / 关键事实字段
- 跨页面的同一实体应有相同 @id（图谱闭环）
- Product 应有 brand.@id 指向 Organization
- Person 应有 worksFor 指向 Organization
- Article 应有 author 指向 Person，publisher 指向 Organization
"""
from __future__ import annotations

from . import make_issue, register


def _v(sig):
    return (sig or {}).get("value")


@register("entity_graph")
def analyze(signals: dict) -> list:
    issues = []
    main = signals.get("main_page") or {}
    main_meta = main.get("meta") or {}
    main_url = _v(main_meta.get("url")) or ""
    main_org = _v((main.get("entity") or {}).get("organization"))

    # 全局实体池（跨页聚合）
    g = (signals.get("global") or {}).get("entities") or {}
    all_orgs = g.get("organizations") or []
    all_persons = g.get("persons") or []
    all_products = g.get("products") or []
    all_articles = g.get("articles") or []

    # ─── 规则 1：主页必须有 Organization ────────────────────────
    if not main_org:
        # 判断整站是否有 Organization（若在子页有，主页缺 → Critical；若全站都无 → 已在 schema 报告中体现，不再重复）
        if all_orgs:
            issues.append(make_issue(
                category="entity_graph",
                kind="homepage_missing_organization",
                severity="Critical",
                title="主页缺 Organization schema（其他页面有）",
                evidence={
                    "main_url": {"value": main_url, "location": "homepage"},
                    "other_pages_with_org": {
                        "value": [o.get("page_url") for o in all_orgs[:5]],
                        "location": "global",
                    },
                },
                impact="主页是品牌的「官方声明」页，AI 模型构建品牌实体时优先看主页；主页缺 Organization 意味着品牌识别依赖子页，权重分散且可能产生实体冲突。",
                fix="在主页 <head> 中补 Organization JSON-LD，含 name/url/logo/sameAs/foundingDate/address/contactPoint 等字段；@id 用 https://domain/#org 稳定片段标识符。",
                layer="template",
                page_url=main_url,
            ))

    # ─── 规则 2：Organization 必须有 sameAs（整站视角） ──────────
    if all_orgs:
        # 取第一个有效 org 检查
        primary = all_orgs[0]
        sameas = primary.get("sameAs")
        sameas_count = (
            len(sameas) if isinstance(sameas, list)
            else (1 if isinstance(sameas, str) else 0)
        )
        if sameas_count == 0:
            issues.append(make_issue(
                category="entity_graph",
                kind="organization_no_sameas",
                severity="Critical",
                title="Organization schema 缺 sameAs，品牌实体无法与外部图谱对齐",
                evidence={
                    "organization": {
                        "value": {"name": primary.get("name"),
                                  "url": primary.get("url"),
                                  "found_on": primary.get("page_url")},
                        "location": "schema.Organization",
                    },
                },
                impact="AI 模型无法将品牌与 Wikipedia/Wikidata/Crunchbase/LinkedIn 等外部实体绑定，品牌被识别为普通字符串而非稳定实体。在「最佳 X 品牌」类 AI 答案中很难被引用。",
                fix="在 Organization schema 中补 sameAs 数组，至少包含 YouTube / LinkedIn / Crunchbase / Trustpilot / Amazon Brand Store 4-8 条真实存在的官方账号。中期推进 Wikidata 词条（需 2 篇以上独立主流媒体报道）。",
                layer="domain",
                page_url=primary.get("page_url", main_url),
            ))

        # 缺关键事实字段
        missing_facts = [f for f in ("foundingDate", "address", "contactPoint")
                         if not primary.get(f)]
        if missing_facts:
            issues.append(make_issue(
                category="entity_graph",
                kind="organization_missing_facts",
                severity="Medium",
                title=f"Organization schema 缺关键事实字段：{', '.join(missing_facts)}",
                evidence={
                    "missing_fields": {"value": missing_facts,
                                       "location": "schema.Organization"},
                    "organization_url": {"value": primary.get("page_url"),
                                         "location": "page"},
                },
                impact="AI 无法抽取品牌创立时间、总部地点、联系方式等事实，知识图谱聚合度低。",
                fix="在 Organization schema 中补 foundingDate / address (PostalAddress) / contactPoint (ContactPoint)。",
                layer="template",
                page_url=primary.get("page_url", main_url),
            ))

    # ─── 规则 3：跨页面 Organization @id 一致性 ─────────────────
    org_ids = {}
    for o in all_orgs:
        name = o.get("name") or ""
        oid = o.get("@id") or "(no @id)"
        org_ids.setdefault(name, {}).setdefault(oid, []).append(o.get("page_url"))

    for name, ids_map in org_ids.items():
        if name and len(ids_map) > 1:
            issues.append(make_issue(
                category="entity_graph",
                kind="organization_id_inconsistent",
                severity="High",
                title=f"Organization「{name}」在不同页面的 @id 不一致",
                evidence={
                    "variants": {"value": [
                        {"@id": k, "pages": v} for k, v in ids_map.items()
                    ]},
                },
                impact="同一品牌在不同页面被识别为不同实体，知识图谱碎片化，AI 无法聚合。",
                fix="统一全站 Organization @id（建议用 https://domain/#org 这种稳定片段标识符），并在 Product/Article/Person 中通过 @id 引用它。",
                layer="domain",
            ))

    # ─── 规则 4：Product.brand 是否有 @type / @id（聚合展示） ─────
    org_id = all_orgs[0].get("@id") if all_orgs else None
    products_no_type = []
    products_no_id_ref = []

    for p in all_products:
        brand = p.get("brand")
        if not isinstance(brand, dict):
            continue
        if not brand.get("@type"):
            products_no_type.append(p)
        if org_id and not brand.get("@id"):
            products_no_id_ref.append(p)

    if products_no_type:
        pages = [p.get("page_url") for p in products_no_type if p.get("page_url")]
        product_names = [p.get("name", "?")[:40] for p in products_no_type]
        issues.append(make_issue(
            category="entity_graph",
            kind="product_brand_no_type",
            severity="High",
            title=f"Product.brand 缺 @type（{len(products_no_type)} 个产品页）",
            evidence={
                "products": {"value": product_names[:5],
                             "location": "Product.brand"},
                "affected_pages": {"value": pages[:5], "location": "multiple pages"},
            },
            impact=f"AI 无法将 Product.brand 解析为 Brand 实体，品牌关联失败。{len(products_no_type)} 个产品页同时存在此问题，说明是模板级 bug。",
            fix='将 Product.brand 改为 {"@type": "Brand", "name": "品牌名", "@id": "https://domain/#brand"}。' +
                f"涉及 {len(products_no_type)} 个产品页，建议在产品详情页模板中一处修复全部。",
            layer="template",
            page_url=pages[0] if pages else main_url,
        ))

    if products_no_id_ref:
        pages = [p.get("page_url") for p in products_no_id_ref if p.get("page_url")]
        product_names = [p.get("name", "?")[:40] for p in products_no_id_ref]
        issues.append(make_issue(
            category="entity_graph",
            kind="product_brand_no_id_ref",
            severity="Medium",
            title=f"Product.brand 未通过 @id 指向 Organization（{len(products_no_id_ref)} 个产品页）",
            evidence={
                "products": {"value": product_names[:5], "location": "Product.brand"},
                "organization_id": {"value": org_id, "location": "Organization.@id"},
                "affected_pages": {"value": pages[:5], "location": "multiple pages"},
            },
            impact=f"Product 与 Organization 是断开的两个节点，AI 无法建立「产品属于品牌」的图谱关系。{len(products_no_id_ref)} 个产品页受影响。",
            fix=f'在 Product.brand 中加 "@id": "{org_id}" 引用 Organization。' +
                f"涉及 {len(products_no_id_ref)} 个产品页，建议改模板统一处理。",
            layer="template",
            page_url=pages[0] if pages else main_url,
        ))

    # ─── 规则 5：Person 是否有 worksFor / sameAs（按人名去重） ──
    seen_persons = {}
    for person in all_persons:
        name = person.get("name")
        if not name:
            continue
        # 同名只报一次，记录所有出现位置
        if name in seen_persons:
            seen_persons[name]["pages"].append(person.get("page_url"))
            continue
        seen_persons[name] = {
            "person": person,
            "pages": [person.get("page_url")],
        }

    for name, info in seen_persons.items():
        person = info["person"]
        pages = [p for p in info["pages"] if p]
        if not person.get("worksFor") and org_id:
            issues.append(make_issue(
                category="entity_graph",
                kind="person_no_worksfor",
                severity="Low",
                title=f"Person schema 缺 worksFor（{name}，出现于 {len(pages)} 个页面）",
                evidence={
                    "person": {"value": name, "location": person.get("location")},
                    "pages": {"value": pages[:5], "location": "pages"},
                },
                impact="作者与品牌之间的雇佣关系未建立，E-E-A-T 信号弱化。",
                fix=f'在 Person schema 中补 "worksFor": {{"@id": "{org_id}"}}。',
                layer="template",
                page_url=pages[0] if pages else main_url,
            ))
        if not person.get("sameAs"):
            # sameAs 对 Person 不是普适规则：仅当作者具备公开身份（外部专家/记者/KOL）
            # 时才是有效信号。企业内部编辑/匿名笔名/AI 占位名没有可挂的 sameAs，
            # 硬造反而触发 AI 反作弊。这里不产出 issue，交给评分 agent 结合作者
            # 身份性质判断（见 prompts/score.md 评分纪律 6.1）。
            pass

    # ─── 规则 6：Article.publisher 是否关联 Organization ─────────
    for art in all_articles:
        pub = art.get("publisher")
        if isinstance(pub, dict) and org_id and not pub.get("@id"):
            issues.append(make_issue(
                category="entity_graph",
                kind="article_publisher_no_id",
                severity="Low",
                title=f"Article.publisher 未通过 @id 关联 Organization（{(art.get('headline') or '')[:40]}）",
                evidence={
                    "article": {"value": art.get("headline"),
                                "location": art.get("location")},
                    "page": {"value": art.get("page_url"), "location": "page"},
                },
                impact="文章与品牌是断开的节点，无法建立「文章由品牌发布」的图谱关系。",
                fix=f'在 Article.publisher 中加 "@id": "{org_id}" 引用 Organization。',
                layer="template",
                page_url=art.get("page_url", main_url),
            ))

    return issues
