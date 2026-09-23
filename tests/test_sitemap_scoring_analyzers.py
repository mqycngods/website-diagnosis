"""sitemap 解析（index + urlset）、ScoreEngine、analyzers 规则 ID。"""
from __future__ import annotations

from diagnose import sitemap
from diagnose.scoring.engine import ScoreEngine, compute_baselines, schema_baseline


class FakeResp:
    def __init__(self, text, status=200):
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status
        self.headers = {}
        self.url = "https://x.com/sitemap.xml"


URLSET = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://x.com/a</loc><lastmod>2026-09-01</lastmod></url>
  <url><loc>https://x.com/b</loc></url>
</urlset>"""

INDEX = """<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://x.com/sitemap-1.xml</loc></sitemap>
</sitemapindex>"""


def test_parse_urlset(monkeypatch):
    monkeypatch.setattr(sitemap, "get", lambda *a, **k: FakeResp(URLSET))
    urls = sitemap.parse_sitemap_urls("https://x.com/sitemap.xml")
    assert [u["url"] for u in urls] == ["https://x.com/a", "https://x.com/b"]
    assert urls[0]["lastmod"] == "2026-09-01"


def test_parse_index_recursion(monkeypatch):
    def fake_get(url, **k):
        return FakeResp(INDEX if url.endswith("sitemap.xml") else URLSET)
    monkeypatch.setattr(sitemap, "get", fake_get)
    urls = sitemap.discover_sitemaps("https://x.com")
    assert urls == ["https://x.com/a", "https://x.com/b"]


def test_schema_baseline():
    schema = {"inventory": [
        {"schema_type": "Organization", "status": "Present", "impact": "Critical"},
        {"schema_type": "Person", "status": "Missing", "impact": "Critical"},
    ], "sameas_count": 5}
    # 1/2 Critical Present = 30 + sameAs 5条 = 40 → 70
    assert schema_baseline(schema) == 70


def test_score_engine_agent_override():
    baselines = compute_baselines(
        citability={"average_citability_score": 60},
        bots={"overall_score": 80}, technical={"security_score": 50},
        schema={"inventory": [], "sameas_count": 0})
    engine = ScoreEngine()
    cards = engine.score(baselines, agent_scores={
        "dimensions": {"citability": {"score": 75, "note": "agent 判断"}}})
    by_key = {c.key: c for c in cards}
    assert by_key["citability"].score == 75 and by_key["citability"].source == "agent"
    assert by_key["platform"].score == 80 and by_key["platform"].source == "deterministic"
    geo = engine.geo_score(cards)
    assert 0 < geo <= 100


def test_analyzer_rule_ids():
    from diagnose import analyzers
    issue = analyzers.make_issue(
        category="entity_graph", kind="organization_no_sameas",
        severity="Critical", title="t", evidence={})
    assert issue["id"] == "GEO-ENT-002"
    # 未登记的 kind 不产出假 ID
    issue2 = analyzers.make_issue(
        category="x", kind="unknown_kind", severity="Low", title="t", evidence={})
    assert issue2["id"] == ""
