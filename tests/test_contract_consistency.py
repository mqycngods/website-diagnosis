"""第三波契约修复的回归锁定（2026-11）。

覆盖：
  1. site_rescan 接受平铺与嵌套两种 agent_picks 形态
  2. rescan 选择数超上限时截断并回显
  3. 维度别名表覆盖现名（爬虫可达性 / 品牌识别与 Schema）
  4. citability 采集失败不派生「均分 0」建议
  5. 契约文件（json / md / prompt）关键约束的一致性
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# ── 1. rescan picks 形态兼容 ────────────────────────────────

def _parse_picks(body: dict) -> dict:
    """复刻 api_server 中 rescan 的 picks 归一逻辑。"""
    import sys
    sys.path.insert(0, str(ROOT))
    from tools.api_server import _as_json

    picks = _as_json(body.get("agent_picks"))
    if not isinstance(picks, dict):
        flat = {k: _as_json(body.get(k)) for k in ("articles", "products")}
        picks = {k: v for k, v in flat.items() if v}
    if not isinstance(picks, dict):
        picks = {}
    return picks


def test_picks_flat_shape():
    """工具声明的平铺形态必须被识别。"""
    body = {"run_id": "r1",
            "articles": '["https://a.com/1","https://a.com/2"]',
            "products": '["https://a.com/p"]'}
    picks = _parse_picks(body)
    assert picks["articles"] == ["https://a.com/1", "https://a.com/2"]
    assert picks["products"] == ["https://a.com/p"]


def test_picks_nested_shape_still_supported():
    """旧提示词的嵌套形态继续可用（向后兼容）。"""
    body = {"run_id": "r1",
            "agent_picks": {"articles": ["https://a.com/1"],
                            "products": ["https://a.com/p"]}}
    picks = _parse_picks(body)
    assert picks["articles"] == ["https://a.com/1"]
    assert picks["products"] == ["https://a.com/p"]


def test_picks_empty_when_absent():
    assert _parse_picks({"run_id": "r1"}) == {}


# ── 2. rescan 截断 ─────────────────────────────────────────

def test_rescan_truncates_and_reports(monkeypatch):
    from diagnose import pipeline

    grabbed = {}

    def fake_fetch_into(articles, article_urls, product_urls, delay):
        grabbed["articles"] = list(article_urls)
        grabbed["products"] = list(product_urls)

    monkeypatch.setattr(pipeline, "_fetch_urls_into", fake_fetch_into)
    monkeypatch.setattr(pipeline, "_ensure_cache", lambda: None)

    picks = {"articles": [f"https://a.com/{i}" for i in range(20)],
             "products": [f"https://a.com/p{i}" for i in range(10)]}
    out = pipeline.rescan_pages(picks, {"url": "https://a.com", "status_code": 200})

    assert len(grabbed["articles"]) <= 5
    assert len(grabbed["products"]) <= 3
    assert out["truncated"], "超量时必须回显截断信息"
    assert out["truncated"]["articles"] > 0


# ── 3. 维度别名 ────────────────────────────────────────────

def test_dimension_alias_covers_current_names():
    """agent 用 list 形式写 dimensions 时，现名必须能映射上。"""
    import sys
    sys.path.insert(0, str(ROOT))
    from diagnose.scoring.engine import ScoreEngine

    engine = ScoreEngine()
    cards = engine.score({}, agent_scores={"dimensions": [
        {"name": "爬虫可达性", "score": 90},
        {"name": "品牌识别与 Schema", "score": 40},
        {"name": "内容引用度", "score": 55},
    ]})
    by_key = {c.key: c for c in cards}
    assert by_key["platform"].score == 90, "爬虫可达性应映射到 platform"
    assert by_key["schema"].score == 40, "品牌识别与 Schema 应映射到 schema"
    assert by_key["citability"].score == 55


# ── 4. citability 失败不派生建议 ────────────────────────────

def test_citability_error_yields_no_recs():
    import sys
    sys.path.insert(0, str(ROOT))
    from diagnose.recommendations.engine import _derive_citability_page_recs

    assert _derive_citability_page_recs(
        {"error": "抓到 WAF 质询页（HTTP 403），本次未测量"}) == []
    assert _derive_citability_page_recs({}) == []
    assert _derive_citability_page_recs(None) == []
    # 正常低分仍应产出建议
    assert _derive_citability_page_recs({"average_citability_score": 20})


# ── 5. 契约文件一致性 ──────────────────────────────────────

def _agent_json() -> dict:
    return json.loads((ROOT / "website_analyze/website_analyze-agent.json").read_text("utf-8"))


def test_render_requires_four_params():
    cfg = _agent_json()
    tool = next(t for t in cfg["tools"] if t["tool_name_en"] == "site_render")
    fields = {p["field"]: p for p in tool["input_params"]}
    assert set(fields) == {"run_id", "url", "scores", "plan_md"}
    assert all(p["required"] for p in fields.values()), "四个参数必须全部 required"


def test_diagnose_run_url_required():
    cfg = _agent_json()
    tool = next(t for t in cfg["tools"] if t["tool_name_en"] == "diagnose_run")
    assert tool["input_params"][0]["required"] is True


def test_rescan_body_is_flat():
    """rescan 的 body 模板不能再用嵌套 agent_picks（与工具声明不一致）。"""
    cfg = _agent_json()
    tool = next(t for t in cfg["tools"] if t["tool_name_en"] == "site_rescan")
    body = tool["url_config"]["body"]
    assert "agent_picks" not in body, "body 模板应为平铺形态"
    assert '"articles"' in body and '"products"' in body


def test_temperature_lowered():
    """逐字誊写 HTML 的任务不需要 0.7 的发散度。"""
    assert _agent_json()["agent"]["temperature"] <= 0.3


def test_no_dead_shell_instructions():
    """agent 没有代码执行工具，提示词不能要求跑 python3 / Bash。"""
    md = (ROOT / "website_analyze/website_analyze-agent.md").read_text("utf-8")
    assert "json.tool" not in md, "删除不可执行的自验指令"
    audit = (ROOT / "website_analyze/skill_resource/website_audit_prompt.md").read_text("utf-8")
    assert "用 Bash 重抓" not in audit


def test_failure_path_still_outputs_json_array():
    """渲染失败也必须输出 JSON 数组，否则平台解析不到失败原因。"""
    md = (ROOT / "website_analyze/website_analyze-agent.md").read_text("utf-8")
    assert "不要走第 7 步的输出格式" not in md


def test_no_base64_channel():
    """base64 逃生通道已移除：服务端不再解码，提示词不再提供该用法。"""
    src = (ROOT / "tools/api_server.py").read_text("utf-8")
    assert "base64" not in src.lower(), "服务端不应再有 base64 解码逻辑"
    md = (ROOT / "website_analyze/website_analyze-agent.md").read_text("utf-8")
    # 允许出现一句禁止性说明（「不做任何 Base64 编码」），但不能有可用的调用方式
    assert 'encoding: "base64"' not in md and "encoding='base64'" not in md
    assert "encoding\" `: \"base64\"" not in md
