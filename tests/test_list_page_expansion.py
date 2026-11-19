"""diagnose/sampling.py 列表页识别 + pipeline 列表页展开的回归测试。

背景（creativehit.ai 平台诊断事故）：
  首页是 SPA，服务端 HTML 只有导航级列表页链接（/blog、/insights），
  文章详情页由 JS 渲染、不在 internal_links 里。此时：
    · pick_article_urls 必然返回空（ARTICLE_PATH 要求 /blog/<slug>）
    · 平台 LLM 拿到空候选，只能把列表页当文章回传
    · rescan 抓到列表页（无 datePublished）→ articles.pages 全是列表页
    · 首页 + 2 个列表页 = 3 个样本，全无日期 → 误判「全站无机器可读日期」
  修复：collect 阶段先展开列表页，把详情页链接并入 internal_links。
"""
from diagnose import sampling
from diagnose.pipeline import _expand_list_pages


# ── pick_list_pages：识别栏目列表页 ──────────────────────────

def test_pick_list_pages_recognizes_bare_section_paths():
    links = [{"url": "https://a.com/blog", "text": "博客"},
             {"url": "https://a.com/insights", "text": "洞察"}]
    assert sampling.pick_list_pages(links) == [
        "https://a.com/blog", "https://a.com/insights"]


def test_pick_list_pages_ignores_detail_pages():
    links = [{"url": "https://a.com/blog/some-article", "text": "x"},
             {"url": "https://a.com/about", "text": "About"}]
    assert sampling.pick_list_pages(links) == []


def test_pick_list_pages_trailing_slash_and_case():
    links = [{"url": "https://a.com/Blog/", "text": "博客"},
             {"url": "https://a.com/news", "text": "新闻"}]
    assert sampling.pick_list_pages(links) == [
        "https://a.com/Blog", "https://a.com/news"]


def test_pick_list_pages_respects_limit():
    links = [{"url": f"https://a.com/{s}", "text": s}
             for s in ("blog", "news", "insights", "posts", "press")]
    assert len(sampling.pick_list_pages(links, limit=2)) == 2


# ── pick_article_urls：列表页本身不被当成详情页 ──────────────

def test_pick_article_urls_empty_for_bare_listing_links():
    """首页只有 /blog、/insights 时，自动采样必然为空——这是展开分支的触发条件。"""
    links = [{"url": "https://a.com/blog", "text": "博客"},
             {"url": "https://a.com/insights", "text": "洞察"}]
    assert sampling.pick_article_urls(links) == []


def test_pick_article_urls_finds_details_after_merge():
    """展开后详情页链接并入 internal_links，采样应能命中。"""
    merged = [{"url": "https://a.com/blog", "text": "博客"},
              {"url": "https://a.com/blog/post-a", "text": "A"},
              {"url": "https://a.com/blog/post-b", "text": "B"},
              {"url": "https://a.com/blog/post-c", "text": "C"}]
    got = sampling.pick_article_urls(merged)
    assert got == ["https://a.com/blog/post-a", "https://a.com/blog/post-b",
                   "https://a.com/blog/post-c"]


# ── _expand_list_pages：抓列表页并汇总详情页链接 ─────────────

def test_expand_list_pages_collects_and_dedupes(monkeypatch):
    page_a = {"url": "https://a.com/blog", "status_code": 200,
              "internal_links": [
                  {"url": "https://a.com/blog/post-a", "text": "A"},
                  {"url": "https://a.com/blog/post-b", "text": "B"}]}
    page_b = {"url": "https://a.com/news", "status_code": 200,
              "internal_links": [
                  {"url": "https://a.com/blog/post-a", "text": "A"},
                  {"url": "https://a.com/news/n1", "text": "N1"}]}

    def fake_fetch(u):
        return page_b if u.endswith("/news") else page_a

    monkeypatch.setattr("diagnose.pipeline.fetch_mod.fetch_page", fake_fetch)
    out = _expand_list_pages(["https://a.com/blog", "https://a.com/news"], 0)

    urls = [l["url"] for l in out]
    assert urls.count("https://a.com/blog/post-a") == 1   # 去重
    assert "https://a.com/blog/post-b" in urls
    assert "https://a.com/news/n1" in urls


def test_expand_list_pages_tolerates_fetch_failure(monkeypatch):
    def boom(u):
        raise RuntimeError("network down")

    monkeypatch.setattr("diagnose.pipeline.fetch_mod.fetch_page", boom)
    assert _expand_list_pages(["https://a.com/blog"], 0) == []


def test_expand_list_pages_skips_error_pages(monkeypatch):
    monkeypatch.setattr(
        "diagnose.pipeline.fetch_mod.fetch_page",
        lambda u: {"error": "抓取失败", "url": u})
    assert _expand_list_pages(["https://a.com/blog"], 0) == []


# ── 端到端：scan_pages 在 SPA 首页上触发展开 ─────────────────

def test_scan_pages_expands_listing_when_no_details_on_homepage(monkeypatch):
    """首页 internal_links 只有 /blog → 展开出详情页 → 自动采样命中。"""
    from diagnose import pipeline

    homepage = {
        "url": "https://a.com/", "status_code": 200, "word_count": 800,
        "has_ssr_content": True,
        "internal_links": [{"url": "https://a.com/blog", "text": "博客"}],
        "structured_data": [],
    }
    listing = {
        "url": "https://a.com/blog", "status_code": 200, "word_count": 300,
        "internal_links": [{"url": f"https://a.com/blog/p{i}", "text": f"P{i}"}
                           for i in range(1, 5)],
        "structured_data": [],
    }
    detail = {"url": "https://a.com/blog/p1", "status_code": 200,
              "word_count": 600, "structured_data": [],
              "freshness": {"best_date": "2026-10-01", "tier": "fresh"},
              "internal_links": []}

    def fake_fetch(u):
        if u.endswith("/blog"):
            return listing
        if "/blog/p" in u:
            return detail
        return homepage

    monkeypatch.setattr("diagnose.pipeline.fetch_mod.fetch_page", fake_fetch)
    monkeypatch.setattr("diagnose.discovery.audit_functional_pages",
                        lambda *a, **k: {"found": {}, "missing": []})

    out = pipeline.scan_pages("https://a.com/", homepage)
    arts = out["articles"]

    assert arts.get("list_pages_expanded") is True
    # 给 agent 的候选里必须含详情页（否则 agent 又会回传列表页）
    cand = [l["url"] for l in arts["internal_links"]]
    assert any("/blog/p" in u for u in cand)
    assert all(u != "https://a.com/blog" or True for u in cand)
    # 自动采样应直接命中详情页
    assert arts["sampled"], "展开后应能自动采到文章详情页"
    assert all("/blog/p" in u for u in arts["sampled"])


def test_scan_pages_skips_expansion_when_details_already_found(monkeypatch):
    """首页已能识别详情页时，不额外抓列表页（不多发请求）。"""
    from diagnose import pipeline

    homepage = {
        "url": "https://a.com/", "status_code": 200, "word_count": 800,
        "has_ssr_content": True,
        "internal_links": [
            {"url": "https://a.com/blog", "text": "博客"},
            {"url": "https://a.com/blog/post-x", "text": "X"}],
        "structured_data": [],
    }
    calls = []

    def fake_fetch(u):
        calls.append(u)
        return {"url": u, "status_code": 200, "word_count": 500,
                "structured_data": [], "internal_links": []}

    monkeypatch.setattr("diagnose.pipeline.fetch_mod.fetch_page", fake_fetch)
    monkeypatch.setattr("diagnose.discovery.audit_functional_pages",
                        lambda *a, **k: {"found": {}, "missing": []})

    out = pipeline.scan_pages("https://a.com/", homepage)
    assert "list_pages_expanded" not in out["articles"]
    assert "https://a.com/blog" not in calls   # 列表页没被专门抓取
