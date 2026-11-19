"""第一波正确性修复的回归锁定（2026-11）。

每个用例对应一个曾能在线上复现的错误结论，防止回退：
  1. 抓取失败形状（只写 errors 列表、status_code=None）必须触发 fetch_failed
  2. 中文正文的 word_count 不能是 1（否则薄内容/≥800 词类规则对中文站失效）
  3. 基线抓取 UA 不能是训练类爬虫（否则健康出版方被误判 P0）
  4. cap_severity_p0 只认实际命中的 blocker key，不受 agent 自报 kind 与标题猜测影响
  5. 兜底 findings 必须过同一把 P0 尺子
  6. 中文关键词去重不误杀（_is_overlapping）
"""
from __future__ import annotations

import pytest

from diagnose.blockers import cap_severity_p0, compute_p0_blockers
from diagnose.text_metrics import word_count


# ── 1. 抓取失败双形状 ────────────────────────────────────────

def test_fetch_page_failure_writes_top_level_error(monkeypatch):
    """fetch_page 内部异常时必须写顶层 error，否则 fetch_failed 永不触发。"""
    from diagnose import fetch as fetch_mod

    def boom(*a, **k):
        raise RuntimeError("DNS 解析失败")

    monkeypatch.setattr(fetch_mod, "get", boom)
    result = fetch_mod.fetch_page("https://nonexistent.invalid/")
    assert result.get("error"), "抓取异常后必须有顶层 error"
    assert result.get("status_code") is None

    blockers = compute_p0_blockers(fetch=result, bots={}, schema={},
                                   issues={}, articles={})
    assert any(b["key"] == "fetch_failed" and b["confidence"] == "deterministic"
               for b in blockers), "真实失败形状必须被判为确定性阻塞"


def test_fetch_errors_only_shape_still_blocks():
    """只带 errors 列表 + status_code=None 的兜底形状同样算抓取失败。"""
    shape = {"url": "https://x.com", "status_code": None,
             "errors": ["抓取失败：连接被重置"], "word_count": 0,
             "has_ssr_content": True, "structured_data": []}
    blockers = compute_p0_blockers(fetch=shape, bots={}, schema={},
                                   issues={}, articles={})
    assert any(b["key"] == "fetch_failed" for b in blockers)


def test_successful_fetch_has_no_error_key():
    """正常抓取不应带 error 键（避免误判）。"""
    shape = {"url": "https://x.com", "status_code": 200, "errors": [],
             "word_count": 800, "has_ssr_content": True,
             "structured_data": [{"@type": "Organization"}]}
    assert "error" not in shape
    blockers = compute_p0_blockers(fetch=shape, bots={}, schema={},
                                   issues={}, articles={})
    assert not any(b["key"] == "fetch_failed" for b in blockers)


# ── 2. 中文词数口径 ─────────────────────────────────────────

def test_word_count_cjk():
    zh = "这是一段中文首页正文。" * 60          # 720 个中文左右的字符
    assert word_count(zh) > 100, "中文正文必须按字符计，不能恒为 1"
    en = "This is an English sentence with eight words."
    assert word_count(en) == 8
    mixed = "GEO 诊断报告 " + "中文内容" * 50
    assert word_count(mixed) >= 200


def test_chinese_homepage_not_judged_thin():
    """中文官网（1000+ 字正文）不应被判 thin_content。"""
    fetch = {"url": "https://x.com", "status_code": 200, "errors": [],
             "word_count": word_count("中文正文内容" * 300),
             "has_ssr_content": True, "structured_data": [{"@type": "Organization"}],
             "h1_tags": ["首页"], "heading_structure": [{"level": 1, "text": "首页"}]}
    blockers = compute_p0_blockers(fetch=fetch, bots={}, schema={},
                                   issues={}, articles={})
    assert not any(b["key"] in ("thin_content", "no_heading_structure") for b in blockers)


# ── 3. 基线抓取 UA ──────────────────────────────────────────

def test_default_ua_is_not_training_crawler():
    """基线抓取不能自报训练类爬虫，否则健康出版方会被误判 P0。"""
    from diagnose.http_utils import DEFAULT_HEADERS
    ua = DEFAULT_HEADERS["User-Agent"].lower()
    for bad in ("gptbot", "claudebot", "ccbot", "google-extended", "bytespider"):
        assert bad not in ua, f"基线 UA 不应包含 {bad}"


def test_bots_probe_uses_per_bot_ua_not_baseline():
    """可达性检测必须逐个显式传各爬虫 UA；基线只是内容参照物。

    这条锁住职责边界：基线 UA 换成浏览器后，probe_bots 仍要跑满 12 个
    爬虫 UA，不能因为基线变了就少测。
    """
    from diagnose import bots as bots_mod
    from diagnose.crawlers import AI_CRAWLERS

    seen_uas = []

    class _Resp:
        status_code = 200

        def __init__(self, text):
            self.text = text
            self.headers = {}
            self.content = text.encode("utf-8")

    def fake_get(url, timeout=30, headers=None):
        ua = (headers or {}).get("User-Agent")
        seen_uas.append(ua)
        # 基线（无 headers）返回正常页面；探针一律返回同样内容（未拦）
        return _Resp("<html><body>" + "content " * 200 + "</body></html>")

    orig = bots_mod.get
    bots_mod.get = fake_get
    try:
        result = bots_mod.probe_bots("https://example.com/", timeout=1, delay=0)
    finally:
        bots_mod.get = orig

    probe_uas = [u for u in seen_uas if u]
    assert len(result["probes"]) == len(AI_CRAWLERS), "12 个爬虫必须全部实测"
    for meta in AI_CRAWLERS.values():
        assert meta["ua"] in probe_uas, f"缺少 {meta['ua'][:40]} 的实测"
    # 基线请求不带 UA 头 → 走 DEFAULT_HEADERS（浏览器身份）
    assert None in seen_uas, "基线请求应不带显式 UA，由 DEFAULT_HEADERS 提供"


# ── 4. cap_severity_p0 以 blockers 为准 ─────────────────────

BLOCKERS_EMPTY: list = []


def test_cap_requires_actual_blocker_hit():
    """agent 自报 kind=no_ssr 但实测无该阻塞 → Critical 必须降级。"""
    assert cap_severity_p0("Critical", "no_ssr", BLOCKERS_EMPTY) == "High"


def test_cap_allows_when_blocker_present():
    blockers = [{"key": "no_ssr", "confidence": "deterministic"}]
    assert cap_severity_p0("Critical", "no_ssr", blockers) == "Critical"


def test_cap_rejects_inferred_blocker():
    """推断类阻塞即使命中也不算 P0。"""
    blockers = [{"key": "thin_content", "confidence": "inferred"}]
    assert cap_severity_p0("Critical", "thin_content", blockers) == "High"


def test_cap_backward_compatible_without_blockers():
    """未传 blockers 时退回白名单常量。"""
    assert cap_severity_p0("Critical", "fetch_failed") == "Critical"
    assert cap_severity_p0("Critical", "organization_no_sameas") == "High"


# ── 5. 兜底 findings 过 P0 尺子 ─────────────────────────────

def test_fallback_findings_respect_blockers(tmp_path):
    """无 scores.json 时，只拦训练类爬虫不能产出 P0 卡片。"""
    import json as _json

    from render.build import build_report

    work = tmp_path / "work"
    work.mkdir()

    def _w(name, data):
        (work / name).write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")

    _w("fetch.json", {
        "url": "https://example.com/", "status_code": 200, "title": "Acme",
        "meta_tags": {}, "h1_tags": ["Acme"], "word_count": 900,
        "structured_data": [{"@type": "Organization", "name": "Acme"}],
        "freshness": {"tier": "unknown"}, "has_ssr_content": True,
        "internal_links": [], "errors": [],
    })
    _w("robots.json", {"exists": True, "ai_crawler_status": {}, "sitemaps": []})
    # 只拦训练类：GPTBot/ClaudeBot
    _w("bots.json", {"probes": [
        {"bot": "GPTBot", "class": "training", "status": 403, "blocked": True},
        {"bot": "ClaudeBot", "class": "training", "status": 403, "blocked": True},
    ], "class_scores": {}, "verdict": "HEALTHY_PUBLISHER", "overall_score": 90})
    _w("citability.json", {"average_citability_score": 60, "all_blocks": [],
                           "total_blocks_analyzed": 1})
    _w("articles.json", {"sampled": [], "pages": [], "product_pages": []})
    _w("pages.json", {"found": {}, "missing": [], "sources": {}})
    _w("schema.json", {"inventory": [{"schema_type": "Organization", "status": "Present",
                                      "impact": "Critical"}],
                       "sameas_count": 2, "types_found": ["Organization"],
                       "has_organization": True, "sampled_pages": 1})
    _w("technical.json", {"https": True, "security_score": 80,
                          "security_matrix": [], "llms": {}})

    report = build_report(work, domain="example.com", url="https://example.com/")
    critical = [f for f in report.findings if f.severity == "Critical"]
    assert not critical, f"训练类爬虫被拦不应产生 Critical：{[f.title for f in critical]}"
