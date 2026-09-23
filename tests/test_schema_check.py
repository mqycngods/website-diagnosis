"""schema_check 四态模型 + Article 家族归并 + 电商识别 + 真实 broken fixture 回归。"""
from __future__ import annotations

import json
from pathlib import Path

from diagnose.schema_check import check_schema, detect_ecommerce

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _row(inv, name):
    return next((i for i in inv if i.get("schema_type") == name), None)


def test_present_missing_notsampled():
    sd = [{"@type": "Organization", "name": "Acme"}]
    # 未采文章页/产品页 → Article/Product 应为 NotSampled 而非 Missing
    out = check_schema(sd, sampled={"homepage": 1, "article_pages": 0, "product_pages": 0})
    assert _row(out["inventory"], "Organization")["status"] == "Present"
    assert "NotSampled" in _row(out["inventory"], "Article（含 NewsArticle/BlogPosting）")["status"]
    assert _row(out["inventory"], "Product")["status"] == "NotSampled"


def test_article_family_merge():
    # BlogPosting 是 Article 子类型——任一检出即文章级覆盖，只出一行
    sd = [{"@type": "BlogPosting", "headline": "x"}]
    out = check_schema(sd, sampled={"homepage": 1, "article_pages": 3, "product_pages": 0})
    art = _row(out["inventory"], "Article（含 NewsArticle/BlogPosting）")
    assert art["status"] == "Present"
    assert _row(out["inventory"], "BlogPosting") is None


def test_broken_beats_present():
    sd = [{"@type": "WebSite", "__parse_error": True, "__error": "Expecting ,",
           "__page_url": "https://x.com/", "__raw_preview": "{"}]
    out = check_schema(sd, sampled={"homepage": 1, "article_pages": 0, "product_pages": 0})
    row = _row(out["inventory"], "WebSite")
    assert row["status"] == "Broken"
    assert row["impact"] == "Critical"
    assert out["broken_blocks_count"] == 1


def test_ecommerce_upgrade():
    links = [{"url": "https://x.com/products/a", "text": "a"}]
    assert detect_ecommerce(links) is True
    sd = [{"@type": "Organization"}]
    out = check_schema(sd, sampled={"homepage": 1, "article_pages": 0, "product_pages": 2},
                       is_ecommerce=True)
    assert _row(out["inventory"], "Product")["impact"] == "High"


def test_real_broken_fixture():
    """真实案例回归：blog.edgelight.com 的 invalid_jsonld 必须检出为 Broken。"""
    fx = json.loads((FIXTURES / "broken_jsonld" / "fetch_errors.json").read_text(encoding="utf-8"))
    errs = fx["errors"]
    assert errs and errs[0]["kind"] == "invalid_jsonld"
    t = errs[0]["schema_type"]
    sd = [{"@type": t, "__parse_error": True, "__error": errs[0].get("error", ""),
           "__page_url": errs[0]["page_url"], "__raw_preview": errs[0].get("raw_preview", "")}]
    out = check_schema(sd, sampled={"homepage": 1, "article_pages": 3, "product_pages": 0},
                       fetch_errors=errs)
    broken_rows = [i for i in out["inventory"] if i["status"] == "Broken"]
    assert broken_rows, "真实 broken JSON-LD 未检出"


def test_autofixed_not_broken():
    """自动修复成功的块不算 Broken，但要计入 autofixed 警告。"""
    fx = json.loads((FIXTURES / "autofixed_comments" / "fetch_errors.json").read_text(encoding="utf-8"))
    errs = fx["errors"]
    assert errs and errs[0]["kind"] == "jsonld_comments" and errs[0]["auto_fixed"]
    out = check_schema([{"@type": errs[0]["schema_type"], "name": "x"}],
                       sampled={"homepage": 1, "article_pages": 0, "product_pages": 0},
                       fetch_errors=errs)
    assert out["broken_blocks_count"] == 0
    assert out["autofixed_count"] >= 1
