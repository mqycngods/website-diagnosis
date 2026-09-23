"""discovery 分类优先级 + 功能页进 signals 管线 + 新规则（h1_missing / FAQ 判定树）。"""
from __future__ import annotations

from diagnose.analyzers import run_all
from diagnose.discovery import _classify, discover_functional_pages
from diagnose.signals import gatherer


# ── discovery：功能关键词优先于 skip 规则 ──────────────────

def test_faq_under_blogs_discovered():
    """回归：/blogs/faq 不被 /blogs/ skip 规则误杀（botslab 实测案例）。"""
    assert _classify("https://x.com/blogs/faq", "") == "faq"


def test_functional_prefix_segments():
    assert _classify("https://x.com/pages/about-botslab", "") == "about"
    assert _classify("https://x.com/pages/warranty-return", "") == "warranty"
    assert _classify("https://x.com/pages/support", "") == "support"


def test_blog_articles_still_skipped():
    """文章详情页不应被误判为功能页（防误报底线）。"""
    assert _classify("https://x.com/blogs/blog/top-10-dash-cams", "") is None
    assert _classify("https://x.com/products/g980h", "") is None
    assert _classify("https://x.com/collections/all", "") is None


def test_anchor_text_chinese():
    assert _classify("https://x.com/pages/xyz", "关于我们") == "about"


# ── 功能页进 signals 管线 ──────────────────────────────────

def _page(url, h1=None, sd=None, words=500):
    return {
        "url": url, "status_code": 200, "title": "T", "meta_tags": {},
        "h1_tags": h1 or [], "heading_structure": [], "word_count": words,
        "text_content": "x " * words, "internal_links": [], "external_links": [],
        "images": [], "structured_data": sd or [], "errors": [],
    }


def test_functional_pages_enter_signals():
    main = _page("https://x.com/", h1=["Home"])
    articles = {"pages": [], "product_pages": []}
    fn = {"support": _page("https://x.com/pages/support", h1=[]),
          "about": _page("https://x.com/pages/about", h1=[])}
    sig = gatherer.gather_all(main, articles, functional_pages=fn)
    assert len(sig["functional_pages"]) == 2
    assert sig["functional_pages"][0]["page_type"] in ("support", "about")
    assert sig["site"]["functional"]["support"]["url"] == "https://x.com/pages/support"


def test_h1_missing_rule_fires_and_aggregates():
    """support + about 同缺 H1 → 聚合为一条 GEO-COV-007。"""
    main = _page("https://x.com/", h1=["Home"])
    articles = {"pages": [], "product_pages": []}
    fn = {"support": _page("https://x.com/pages/support", h1=[]),
          "about": _page("https://x.com/pages/about", h1=[])}
    sig = gatherer.gather_all(main, articles, functional_pages=fn)
    issues = run_all(sig)
    h1_issues = [i for i in issues["coverage"] if i["kind"] == "h1_missing"]
    assert len(h1_issues) == 1, "同 kind 多页应聚合为一条"
    assert h1_issues[0]["id"] == "GEO-COV-007"
    assert h1_issues[0]["page_count"] == 2


def test_functional_pages_spare_content_rules():
    """作用域：功能页不触发 argumentation 内容型规则（防噪音）。"""
    main = _page("https://x.com/", h1=["Home"], words=100)
    articles = {"pages": [], "product_pages": []}
    fn = {"support": _page("https://x.com/pages/support", h1=["Support"], words=2000)}
    sig = gatherer.gather_all(main, articles, functional_pages=fn)
    issues = run_all(sig)
    arg_urls = [i.get("page_url") for i in issues["argumentation"]]
    assert "https://x.com/pages/support" not in arg_urls


# ── FAQ 判定树（GEO-COV-008） ──────────────────────────────

def _sig_with_faq(faq_page, faq_elsewhere=False):
    main = _page("https://x.com/", h1=["Home"],
                 sd=[{"@type": "FAQPage"}] if faq_elsewhere else [])
    articles = {"pages": [], "product_pages": []}
    fn = {"faq": faq_page} if faq_page else {}
    return gatherer.gather_all(main, articles, functional_pages=fn)


def test_faq_page_without_schema_fires():
    faq = _page("https://x.com/blogs/faq", h1=["FAQ"], words=620)
    sig = _sig_with_faq(faq)
    issues = run_all(sig)
    hits = [i for i in issues["coverage"] if i["kind"] == "faq_content_without_schema"]
    assert len(hits) == 1
    assert hits[0]["id"] == "GEO-COV-008"
    assert hits[0]["severity"] == "High"


def test_faq_page_with_schema_silent():
    faq = _page("https://x.com/blogs/faq", h1=["FAQ"], words=620,
                sd=[{"@type": "FAQPage"}])
    sig = _sig_with_faq(faq)
    issues = run_all(sig)
    assert not [i for i in issues["coverage"] if i["kind"] == "faq_content_without_schema"]


def test_faq_missing_silent():
    """FAQ 页未发现时脚本不报（建设性建议留给 agent，score.md 分工）。"""
    sig = _sig_with_faq(None)
    issues = run_all(sig)
    assert not [i for i in issues["coverage"] if i["kind"] == "faq_content_without_schema"]


def test_faq_elsewhere_downgrades_to_medium():
    """FAQPage 已在内容页检出时，FAQ 页未标注降为 Medium。"""
    faq = _page("https://x.com/blogs/faq", h1=["FAQ"], words=620)
    sig = _sig_with_faq(faq, faq_elsewhere=True)
    issues = run_all(sig)
    hits = [i for i in issues["coverage"] if i["kind"] == "faq_content_without_schema"]
    assert hits and hits[0]["severity"] == "Medium"
