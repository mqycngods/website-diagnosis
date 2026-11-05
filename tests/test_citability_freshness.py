"""citability 中英词数与段落评分、freshness 分级。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from diagnose.citability import (_iter_blocks, _split_long, _wc, _score_passage)
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


# --- 块提取：不能只认 <p>，div 堆叠的正文同样要能提取 ---

_DIV_ONLY = """<html><body>
<div class="hero">
  <div class="t">Singapore Property for Sale and Rent</div>
  <div class="d">Housebell lists HDB flats, condos and landed houses across Singapore
  with smart filters, interactive maps and bilingual support for every buyer.</div>
</div>
<h2>Why Housebell</h2>
<div class="grid">
  <div class="card"><div class="ct">Emerald Park</div><div class="cp">S$1,630,000</div></div>
</div>
</body></html>"""


def test_blocks_extracted_without_p_tags():
    soup = BeautifulSoup(_DIV_ONLY, "lxml")
    assert soup.find_all("p") == []          # 前提：确实没有 p 标签
    texts = [t for k, t in _iter_blocks(soup)]
    joined = " ".join(texts)
    assert "HDB flats, condos and landed houses" in joined
    assert "Emerald Park" in joined


def test_blocks_not_duplicated_by_nesting():
    # 嵌套的块级元素不能让同一段文字被上层容器重复收集
    soup = BeautifulSoup(_DIV_ONLY, "lxml")
    joined = " ".join(t for _, t in _iter_blocks(soup))
    assert joined.count("Emerald Park") == 1
    assert joined.count("S$1,630,000") == 1


def test_framework_comments_are_not_content():
    # Vue 的 <!--teleport start--> 是注释节点，不能当成正文段落
    soup = BeautifulSoup(
        '<div id="app"><!--teleport start--><div>Real body copy here.</div>'
        '<!--teleport end--></div>', "lxml")
    joined = " ".join(t for _, t in _iter_blocks(soup))
    assert "teleport" not in joined.lower()
    assert "Real body copy here." in joined


def test_hidden_and_link_wall_blocks_skipped():
    soup = BeautifulSoup(
        '<div style="display:none">Hidden promotional copy goes here.</div>'
        '<div class="nav"><a href="/a">HDB for Sale</a><a href="/b">Condos for Sale</a>'
        '<a href="/c">Landed House for Sale</a><a href="/d">View All Properties for Sale</a>'
        '<a href="/e">HDB for Rent</a><a href="/f">Condos for Rent</a>'
        '<a href="/g">Landed House for Rent</a><a href="/h">View All Properties for Rent</a></div>',
        "lxml")
    joined = " ".join(t for _, t in _iter_blocks(soup))
    assert "Hidden promotional" not in joined
    assert "HDB for Sale" not in joined


def test_split_long_returns_paragraph_sized_parts():
    text = " ".join(["This is a reasonably long and complete sentence about property."] * 80)
    assert _wc(text) > 400
    parts = _split_long(text)
    assert len(parts) > 1
    assert all(_wc(p) <= 400 for p in parts)
    # 短文本原样返回
    assert _split_long("A short paragraph stays intact.") == ["A short paragraph stays intact."]
