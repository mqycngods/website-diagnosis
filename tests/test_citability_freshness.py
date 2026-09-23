"""citability 中英词数与段落评分、freshness 分级。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from diagnose.citability import _wc, _score_passage
from diagnose.freshness import _parse_date, extract_freshness


def test_wc_chinese_counts_chars():
    # 中文无空格：必须按字符计，否则整段被算成 1 个「词」
    assert _wc("这是一段中文内容") == 8
    assert _wc("LED display 是一种显示设备") == 2 + 7


def test_score_passage_bounds():
    s = _score_passage("LED 显示屏是一种通过发光二极管阵列成像的设备。" * 3, "什么是 LED 显示屏")
    assert 0 <= s["total_score"] <= 100
    assert s["grade"] in "ABCDEF"
    for k, v in s["breakdown"].items():
        assert v >= 0


def test_parse_date_iso_and_cn():
    assert _parse_date("2026-09-01T10:00:00Z") is not None
    assert _parse_date("2026年9月1日") is not None
    assert _parse_date("not a date") is None


def test_freshness_tiers():
    recent = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    sd = [{"@type": "Article", "dateModified": recent}]
    r = extract_freshness(sd, None, {})
    assert r["tier"] == "fresh"
    assert r["source"] == "structured_data"


def test_freshness_unknown_when_no_dates():
    r = extract_freshness([{"@type": "Organization"}], None, {})
    assert r["tier"] == "unknown"
