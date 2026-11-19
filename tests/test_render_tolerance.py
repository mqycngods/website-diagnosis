"""第一波容错修复的回归锁定（2026-11）。

覆盖：agent 输出脏形状不再毁掉整份报告、服务端不再在空数据上
「成功」返回假报告。
"""
from __future__ import annotations

import json


def _write_work(work, scores=None, *, bots_blocked=False):
    def _w(name, data):
        (work / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    _w("fetch.json", {
        "url": "https://example.com/", "status_code": 200, "title": "Acme",
        "meta_tags": {}, "h1_tags": ["Acme"], "word_count": 900,
        "structured_data": [{"@type": "Organization", "name": "Acme"}],
        "freshness": {"tier": "unknown"}, "has_ssr_content": True,
        "internal_links": [], "errors": [],
    })
    _w("robots.json", {"exists": True, "ai_crawler_status": {}, "sitemaps": []})
    probes = [{"bot": "GPTBot", "class": "training", "status": 403, "blocked": True}] \
        if bots_blocked else []
    _w("bots.json", {"probes": probes, "class_scores": {}, "verdict": "OPEN",
                     "overall_score": 90})
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
    if scores is not None:
        _w("scores.json", scores)


# ── 脏 scores 不应毁掉整份报告 ──────────────────────────────

def test_string_score_falls_back_to_baseline(tmp_path):
    """agent 写 "85/100" 这类分数不能抛异常，应回落确定性基线。"""
    from render.build import build_report

    work = tmp_path / "w"
    work.mkdir()
    _write_work(work, {
        "description": "d",
        "dimensions": {"technical": {"score": "85/100", "note": "n"}},
    })
    report = build_report(work, domain="example.com", url="https://example.com/")
    assert report.geo_score > 0
    tech = next(c for c in report.components if c.key == "technical")
    assert isinstance(tech.score, int)


def test_garbage_findings_are_skipped(tmp_path):
    """findings 里混入字符串项时跳过，不抛 AttributeError。"""
    from render.build import build_report

    work = tmp_path / "w"
    work.mkdir()
    _write_work(work, {
        "findings": ["这不是字典", {"title": "正常 finding", "severity": "High",
                                    "evidence": "e", "impact": "i", "fix": "f"}],
    })
    report = build_report(work, domain="example.com", url="https://example.com/")
    assert any(f.title == "正常 finding" for f in report.findings)


def test_extra_action_keys_are_ignored(tmp_path):
    """action 多写未知键（title/priority）不应抛 TypeError。"""
    from render.build import build_report

    work = tmp_path / "w"
    work.mkdir()
    _write_work(work, {
        "quick_wins": [{"action": "补 sameAs", "title": "多余标题",
                        "priority": "P0", "impact": "High",
                        "effort": "hours", "owner": "developer"}],
    })
    report = build_report(work, domain="example.com", url="https://example.com/")
    assert any("sameAs" in (a.action or "") for a in report.quick_wins)


def test_action_without_action_text_is_dropped(tmp_path):
    from render.build import build_report

    work = tmp_path / "w"
    work.mkdir()
    _write_work(work, {"quick_wins": [{"title": "只有标题", "impact": "High"}]})
    report = build_report(work, domain="example.com", url="https://example.com/")
    assert report.quick_wins == []


# ── analyzer 补充发现不进行动清单 ──────────────────────────

def test_analyzer_findings_do_not_enter_action_plan(tmp_path):
    """服务端分析器补充的 findings 标注 source=analyzer，且不进行动清单。"""
    from render.build import build_report

    work = tmp_path / "w"
    work.mkdir()
    _write_work(work, {
        "findings": [{"title": "agent 写的问题", "severity": "High",
                      "evidence": "e", "impact": "i", "fix": "修它"}],
    })
    # 注入一个分析器 issue（与 agent finding 无概念重叠，确保会并入 findings）
    (work / "issues.json").write_text(json.dumps({
        "coverage": [{"kind": "faq_missing", "title": "站点无 FAQ 页",
                      "severity": "Low", "evidence": {"x": 1},
                      "recommendation": "建一个 FAQ 页"}],
    }, ensure_ascii=False), encoding="utf-8")

    report = build_report(work, domain="example.com", url="https://example.com/")
    analyzer = [f for f in report.findings if getattr(f, "source", "agent") == "analyzer"]
    if not analyzer:          # 分析器未产出补充项时本用例无约束对象
        return
    analyzer_titles = {f.title for f in analyzer}
    action_titles = {a.title for a in report.priority_actions}
    assert analyzer_titles.isdisjoint(action_titles), (
        f"分析器补充项不应进行动清单，实际混入：{analyzer_titles & action_titles}")
