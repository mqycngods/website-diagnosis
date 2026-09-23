"""端到端冒烟：合成 work/ → build_report → render_html，验证引擎-渲染解耦后链路完整。"""
from __future__ import annotations

import json
from pathlib import Path

from render.build import build_report
from render.html import render_html


def _write(d: Path, name: str, data: dict):
    (d / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_render_smoke(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    _write(work, "fetch.json", {
        "url": "https://example.com/", "status_code": 200, "title": "Acme",
        "meta_tags": {"description": "d"}, "h1_tags": ["Acme"],
        "structured_data": [{"@type": "Organization", "name": "Acme",
                             "sameAs": ["https://x.com/a"]}],
        "freshness": {"tier": "unknown", "age_days": None, "best_date": None,
                      "date_published": None, "date_modified": None, "source": None}, "has_ssr_content": True,
        "internal_links": [], "errors": [],
    })
    _write(work, "robots.json", {"url": "https://example.com/robots.txt", "exists": True,
                                 "ai_crawler_status": {"GPTBot": "ALLOWED"}, "sitemaps": []})
    _write(work, "bots.json", {"probes": [
        {"bot": "GPTBot", "class": "training", "status": 200, "blocked": False},
        {"bot": "ClaudeBot", "class": "training", "status": 200, "blocked": False},
        {"bot": "GoogleBot", "class": "traditional-search", "status": 200, "blocked": False},
    ], "class_scores": {"training": 100, "traditional-search": 100},
        "verdict": "OPEN", "overall_score": 90})
    _write(work, "citability.json", {"average_citability_score": 55.0, "all_blocks": [],
                                     "total_blocks_analyzed": 0})
    _write(work, "articles.json", {"sampled": [], "pages": [], "product_pages": []})
    _write(work, "pages.json", {"found": {}, "missing": ["faq"], "sources": {}})
    _write(work, "schema.json", {
        "inventory": [
            {"schema_type": "Organization", "status": "Present", "impact": "Critical"},
            {"schema_type": "Person", "status": "Missing", "impact": "Critical"},
        ], "sameas_count": 3, "types_found": ["Organization"],
        "has_organization": True, "sampled_pages": 1,
    })
    _write(work, "technical.json", {"https": True, "security_score": 60,
                                    "security_matrix": [], "llms": {}})

    report = build_report(work, domain="example.com", url="https://example.com/")
    assert report.geo_score > 0
    assert report.components and all(c.measured for c in report.components)

    out = render_html(report, tmp_path / "report.html")
    html = out.read_text(encoding="utf-8")
    assert "example.com" in html
    assert str(report.geo_score) in html


def test_render_survives_all_errors(tmp_path):
    """所有采集步骤失败（{"error": ...}）时渲染不 crash，显示降级。"""
    work = tmp_path / "work"
    work.mkdir()
    for name in ("fetch", "robots", "bots", "citability", "articles", "pages"):
        _write(work, f"{name}.json", {"error": "模拟失败"})
    report = build_report(work, domain="example.com", url="https://example.com/")
    out = render_html(report, tmp_path / "report.html")
    assert out.exists()
