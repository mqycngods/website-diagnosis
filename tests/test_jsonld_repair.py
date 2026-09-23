"""JSON-LD 四级容错修复的回归测试。

每一级修复都对应真实客户站案例（见 fixtures/）：
  L0 直接解析
  L1 HTML 实体编码污染
  L2 字符串内未转义换行
  L3 内嵌非法 JS 注释（//）
  L4 真的修不好 → __parse_error 占位
"""
from __future__ import annotations

from diagnose import fetch as fetch_mod


class FakeResp:
    status_code = 200
    headers = {}
    url = "https://example.com/"

    def __init__(self, html):
        self.text = html
        self.content = html.encode("utf-8")
        self.encoding = "utf-8"


def _patch_get(monkeypatch, jsonld_raw: str):
    html = (
        '<html><head><title>T</title>'
        f'<script type="application/ld+json">{jsonld_raw}</script>'
        '</head><body><h1>Hello</h1><p>content words here</p></body></html>'
    )
    monkeypatch.setattr("diagnose.http_utils.get", lambda *a, **k: FakeResp(html))
    monkeypatch.setattr("diagnose.fetch.get", lambda *a, **k: FakeResp(html))


def test_l0_valid_jsonld(monkeypatch):
    _patch_get(monkeypatch, '{"@type":"Organization","name":"Acme"}')
    r = fetch_mod.fetch_page("https://example.com/")
    assert r["structured_data"][0]["@type"] == "Organization"
    assert not any(isinstance(e, dict) for e in r["errors"])


def test_l1_html_entities_autofixed(monkeypatch):
    _patch_get(monkeypatch,
               '{&quot;@type&quot;:&quot;Organization&quot;,&quot;name&quot;:&quot;Acme&quot;}')
    r = fetch_mod.fetch_page("https://example.com/")
    assert r["structured_data"][0]["@type"] == "Organization"
    kinds = [e.get("kind") for e in r["errors"] if isinstance(e, dict)]
    assert "jsonld_html_entities" in kinds


def test_l2_unescaped_newline_autofixed(monkeypatch):
    _patch_get(monkeypatch,
               '{"@type":"Article","headline":"Line1\nLine2"}')
    r = fetch_mod.fetch_page("https://example.com/")
    assert r["structured_data"][0]["@type"] == "Article"
    kinds = [e.get("kind") for e in r["errors"] if isinstance(e, dict)]
    assert "jsonld_unescaped_newline" in kinds


def test_l3_js_comments_autofixed(monkeypatch):
    _patch_get(monkeypatch,
               '{\n"@type": "WebSite", // site block\n"name": "Acme"\n}')
    r = fetch_mod.fetch_page("https://example.com/")
    assert r["structured_data"][0]["@type"] == "WebSite"
    kinds = [e.get("kind") for e in r["errors"] if isinstance(e, dict)]
    assert "jsonld_comments" in kinds


def test_l4_truly_broken_placeholder(monkeypatch):
    _patch_get(monkeypatch, '{"@type":"FAQPage","name": broken,,}')
    r = fetch_mod.fetch_page("https://example.com/")
    sd = r["structured_data"][0]
    assert sd.get("__parse_error") is True
    assert sd["@type"] == "FAQPage"  # 类型线索保留
    kinds = [e.get("kind") for e in r["errors"] if isinstance(e, dict)]
    assert "invalid_jsonld" in kinds
