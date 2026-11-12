"""diagnose/blockers.py 的确定性 P0 阻塞判定测试。

覆盖五类阻塞 + 同源边界：
- firstdb 场景：首页零 JSON-LD + 作者署名系统性冲突 → 恰好 2 条阻塞
- 干净站点 → 0 条
- 检索类爬虫被拦 / 无 SSR / 全站无日期 → 各类各 1 条
- sameAs 为空但 Organization 存在 → 不算阻塞（与评分卡口径一致）
"""
from diagnose.blockers import compute_p0_blockers, p0_blocking_note


def _fetch(**over):
    base = {"url": "https://a.com", "status_code": 200, "word_count": 800,
            "has_ssr_content": True, "challenge_detected": False,
            "heading_structure": [{"level": 1, "text": "x"}], "h1_tags": ["x"],
            "structured_data": [{"@type": "Organization"}],
            "freshness": {"best_date": "2026-01-01"}}
    base.update(over)
    return base


def _bots(blocked=()):
    classes = {"GPTBot": "live-retrieval", "OAI-SearchBot": "search-index",
               "ClaudeBot": "training"}
    return {"probes": [{"bot": n, "class": c, "blocked": n in blocked}
                       for n, c in classes.items()]}


def _issues(category, kind, severity):
    return {"x": [{"category": category, "kind": kind, "severity": severity,
                   "title": f"{kind} 标题", "evidence": {}}]}


def _run(fetch=None, bots=None, schema=None, issues=None, articles=None):
    return compute_p0_blockers(
        fetch=fetch if fetch is not None else _fetch(),
        bots=bots if bots is not None else _bots(),
        schema=schema if schema is not None else {"has_organization": True},
        issues=issues if issues is not None else {},
        articles=articles if articles is not None else {})


def test_clean_site_no_blockers():
    assert _run() == []
    assert p0_blocking_note([]) == "可进行 GEO 优化，以下内容可慢慢调整。"


def test_firstdb_scenario_two_blockers():
    """firstdb 案例：首页零 JSON-LD + 身份冲突 → 2 条阻塞（用户预期口径）。"""
    issues = {
        "entity_graph": [{"category": "entity_graph",
                          "kind": "homepage_missing_organization",
                          "severity": "Critical", "title": "主页缺 Organization schema",
                          "evidence": {}}],
        "consistency": [{"category": "consistency",
                         "kind": "author_mismatch_template",
                         "severity": "Critical", "title": "作者署名系统性冲突",
                         "evidence": {}}],
    }
    articles = {"pages": [{"status_code": 200,
                           "freshness": {"best_date": "2025-07-10"}}],
                "product_pages": []}
    out = _run(fetch=_fetch(structured_data=[]), issues=issues, articles=articles)
    keys = {b["key"] for b in out}
    assert keys == {"homepage_zero_jsonld", "consistency:author_mismatch_template"}
    note = p0_blocking_note(out)
    assert "2 项 P0 级阻塞" in note and "可进行 GEO 优化" not in note


def test_homepage_zero_jsonld_dedups_missing_organization():
    """首页零 JSON-LD 必然导致主页缺 Organization——同根因只列一条。"""
    issues = _issues("entity_graph", "homepage_missing_organization", "Critical")
    out = _run(fetch=_fetch(structured_data=[]), issues=issues)
    assert [b["key"] for b in out] == ["homepage_zero_jsonld"]


def test_sameas_empty_not_blocking_when_org_exists():
    """sameAs 空但 Organization 存在 → 非阻塞（评分卡边界口径）。"""
    issues = _issues("entity_graph", "organization_no_sameas", "Critical")
    assert _run(issues=issues) == []


def test_retrieval_bots_blocked():
    out = _run(bots=_bots(blocked={"GPTBot", "OAI-SearchBot"}))
    assert [b["key"] for b in out] == ["retrieval_bots_blocked"]
    assert "GPTBot" in out[0]["evidence"]


def test_training_bot_blocked_not_blocking():
    """训练类爬虫被拦不切断检索通路 → 非阻塞。"""
    assert _run(bots=_bots(blocked={"ClaudeBot"})) == []


def test_no_ssr_blocking():
    out = _run(fetch=_fetch(has_ssr_content=False))
    assert any(b["key"] == "no_ssr" for b in out)


def test_thin_content_blocking():
    assert any(b["key"] == "thin_content" for b in _run(fetch=_fetch(word_count=30)))
    # 有实质正文但可引用性低 → 非阻塞（词数不看 citability 分数）
    assert _run(fetch=_fetch(word_count=400)) == []


def test_all_pages_no_dates_blocking():
    art = {"pages": [{"status_code": 200, "freshness": {}}], "product_pages": []}
    out = _run(fetch=_fetch(freshness={}), articles=art)
    assert any(b["key"] == "no_machine_readable_date" for b in out)


def test_fetch_failure_blocking():
    out = _run(fetch={"error": "连接超时"})
    assert any(b["key"] == "fetch_failed" for b in out)


def test_missing_inputs_no_false_positive():
    """输入缺失/报错时不误判（fetch/bots/schema 为 error dict）。"""
    out = compute_p0_blockers(fetch={"error": "x"}, bots={"error": "x"},
                              schema={"error": "x"}, issues={}, articles={})
    assert [b["key"] for b in out] == ["fetch_failed"]
