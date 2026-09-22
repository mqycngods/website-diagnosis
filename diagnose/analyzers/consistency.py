"""一致性分析：跨字段 / 跨页面事实是否对齐。

通用规则：
- 同一事实在多处出现时，值必须一致（title / og:title / headline / H1）
- 同一实体在多处出现时，标识必须一致（作者名、品牌名、@id）
- canonical / og:url / 实际 URL 必须对齐
"""
from __future__ import annotations

from . import make_issue, register


def _v(sig):
    """从 signal 中取值（处理空）。"""
    return (sig or {}).get("value")


def _norm(s):
    """归一化字符串便于比对。"""
    if not isinstance(s, str):
        return s
    return s.strip().rstrip(".,;:!?|").lower()


@register("consistency")
def analyze(signals: dict) -> list:
    issues = []
    main = signals.get("main_page") or {}

    # ─── 规则 1：作者名跨位置一致性（对每篇文章页都做） ─────────
    invalid_tokens = {"gpt", "descriptive", "notification", "the", "and", "with", "by",
                      "bot", "admin", "team"}

    def _clean(names):
        out = set()
        for n in names or []:
            if not n: continue
            n_lower = n.lower().strip()
            if any(t in n_lower for t in invalid_tokens): continue
            if len(n) < 3 or len(n) > 40: continue
            out.add(n.strip())
        return out

    def _check_author_consistency(page_signals: dict):
        author = page_signals.get("author") or {}
        page_url = page_signals.get("url") or ""
        meta = page_signals.get("meta") or {}
        title = _v(meta.get("title")) or ""

        schema_authors = _clean(_v(author.get("schema_authors")) or [])
        byline_authors = _clean(_v(author.get("byline_authors")) or [])
        bio_authors = _clean(_v(author.get("bio_authors")) or [])
        meta_author = _v(author.get("meta_author"))
        if meta_author:
            byline_authors.update(_clean([meta_author]))

        all_names = schema_authors | byline_authors | bio_authors
        if len(all_names) <= 1:
            return None

        # 找出冲突对：schema 作者 vs About-the-Author 介绍的人
        if schema_authors and bio_authors and not (schema_authors & bio_authors):
            return make_issue(
                category="consistency",
                kind="author_mismatch",
                severity="High",
                title=f"文章作者署名不一致：schema 为「{sorted(schema_authors)[0]}」但 About the Author 介绍「{sorted(bio_authors)[0]}」",
                evidence={
                    "schema_authors": {"value": sorted(schema_authors),
                                       "location": "Article.author / Person.name"},
                    "byline_authors": {"value": sorted(byline_authors),
                                       "location": "HTML byline"},
                    "bio_authors": {"value": sorted(bio_authors),
                                    "location": "HTML About the Author 段落"},
                    "article_title": {"value": title[:80], "location": "page"},
                },
                impact="AI 模型无法确定真实作者，E-E-A-T 信号被打折；Perplexity / Google AIO 在引用作者时可能给出矛盾信息；长期看会损害品牌在知识图谱中的作者实体聚合。",
                fix="内容负责人确认实际作者与编辑角色，统一 HTML 顶部署名、文末 About the Author 段落与 Article schema 中的 author 字段。若存在「作者 + 编辑」分工，用 author + editor 两个字段明确标注，不要混用同一段落。",
                layer="editorial",
                page_url=page_url,
            )
        return None

    # 主页 + 所有文章页都跑一致性检查
    all_pages = [main] + (signals.get("article_pages") or []) + (signals.get("product_pages") or [])
    # 按 (kind, schema_author) 聚合：同一 schema 作者对应多个不同 bio 作者 = 同一模板问题
    author_mismatch_groups = {}  # {(schema_author,): [(bio_author, page_url, title)]}
    other_issues = []

    for pg in all_pages:
        if not pg:
            continue
        issue = _check_author_consistency(pg)
        if not issue:
            continue
        if issue["kind"] == "author_mismatch":
            schema_key = tuple(sorted(issue["evidence"]["schema_authors"]["value"]))
            bio_key = tuple(sorted(issue["evidence"]["bio_authors"]["value"]))
            author_mismatch_groups.setdefault((schema_key,), []).append({
                "bio_authors": bio_key,
                "page_url": issue.get("page_url", ""),
                "title": issue.get("title", ""),
                "evidence": issue.get("evidence", {}),
            })
        else:
            other_issues.append(issue)

    # 把作者冲突聚合：同一 schema_author 对应多个 bio_author 时合并为一条
    for (schema_key,), items in author_mismatch_groups.items():
        if not items:
            continue
        schema_author = schema_key[0] if schema_key else "?"
        # 收集所有 bio 作者 + 所有页面
        all_bio = sorted({b for it in items for b in it["bio_authors"]})
        all_pages_urls = [it["page_url"] for it in items if it["page_url"]]

        if len(items) == 1:
            # 单篇文章冲突：保持原样
            it = items[0]
            issues.append(make_issue(
                category="consistency",
                kind="author_mismatch",
                severity="High",
                title=f"文章作者署名不一致：schema 为「{schema_author}」但 About the Author 介绍「{', '.join(all_bio)}」",
                evidence=it["evidence"],
                impact="AI 模型无法确定真实作者，E-E-A-T 信号被打折；Perplexity / Google AIO 在引用作者时可能给出矛盾信息。",
                fix="内容负责人确认实际作者与编辑角色，统一 HTML 顶部署名、文末 About the Author 段落与 Article schema 中的 author 字段。若存在「作者 + 编辑」分工，用 author + editor 两个字段明确标注。",
                layer="editorial",
                page_url=it["page_url"],
            ))
        else:
            # 多篇文章冲突：聚合为「模板级问题」
            issues.append(make_issue(
                category="consistency",
                kind="author_mismatch_template",
                severity="Critical",
                title=f"作者署名系统性冲突：schema 统一标「{schema_author}」，但 About the Author 段落出现 {len(all_bio)} 个不同人名（{len(items)} 篇文章）",
                evidence={
                    "schema_author": {"value": schema_author,
                                      "location": "Article.author / Person.name"},
                    "bio_authors_found": {"value": all_bio,
                                          "location": "HTML About the Author 段落"},
                    "affected_pages": {"value": all_pages_urls[:5],
                                       "location": "multiple pages"},
                },
                impact=f"这是模板级问题：{len(items)} 篇文章的 schema 都标「{schema_author}」，但 About the Author 段落轮换使用 {len(all_bio)} 个不同的人名（{', '.join(all_bio[:3])} 等）。AI 模型在聚合作者实体时会得到 {len(all_bio)}+1 个互相矛盾的信号，整个作者体系的 E-E-A-T 全部失效。",
                fix=f"【模板级问题】这通常意味着 About the Author 是从模板复制但忘了改人名。\n\n1. 内容负责人逐篇确认实际作者与编辑角色\n2. 若「{schema_author}」是真作者，把 About the Author 段落里的虚假人名全部改为真实作者 bio\n3. 若存在「作者 + 编辑」分工，用 Article.author + Article.editor 两个字段明确分开，不要在 About the Author 段落里混用\n4. 涉及页面：{chr(10).join(all_pages_urls[:5])}{' 等' if len(all_pages_urls) > 5 else ''}",
                layer="template",
                page_url=all_pages_urls[0] if all_pages_urls else "",
            ))

    issues.extend(other_issues)

    # ─── 规则 2-4：meta/canonical/og 一致性（只对主页做，避免噪音） ──
    meta = main.get("meta") or {}

    # title vs og:title 一致性
    title = _norm(_v(meta.get("title")))
    og_title = _norm(_v(meta.get("og_title")))
    if title and og_title and title != og_title:
        if abs(len(title) - len(og_title)) / max(len(title), len(og_title), 1) > 0.3:
            issues.append(make_issue(
                category="consistency",
                kind="title_mismatch",
                severity="Low",
                title="title 与 og:title 差异过大",
                evidence={
                    "title": {"value": _v(meta.get("title")), "location": "HTML <title>"},
                    "og_title": {"value": _v(meta.get("og_title")),
                                 "location": '<meta property="og:title">'},
                },
                impact="社交平台分享时展示的标题与 SERP 标题差异过大，可能让 AI 检索到的摘要与视觉标题不一致。",
                fix="确认 og:title 是否有意简化；若无，保持与 title 一致。",
                layer="template",
                page_url=_v(meta.get("url")) or "",
            ))

    # canonical vs og:url
    canonical = _v(meta.get("canonical"))
    og_url = _v(meta.get("og_url"))
    actual_url = _v(meta.get("url"))
    if canonical and og_url and canonical != og_url:
        issues.append(make_issue(
            category="consistency",
            kind="canonical_og_url_mismatch",
            severity="Medium",
            title="canonical 与 og:url 不一致",
            evidence={
                "canonical": {"value": canonical, "location": '<link rel="canonical">'},
                "og_url": {"value": og_url, "location": '<meta property="og:url">'},
            },
            impact="搜索引擎按 canonical 判定主页面，社交平台按 og:url 分享——两者不一致可能导致权重分散。",
            fix="让 og:url 与 canonical 保持完全一致。",
            layer="template",
            page_url=actual_url or "",
        ))

    return issues
