"""diagnose/blockers.py 的确定性 P0 阻塞判定测试。

覆盖五类阻塞 + 同源边界 + 改造后的六原则：
- firstdb 场景：首页零 JSON-LD + 作者署名系统性冲突 → 恰好 2 条阻塞
- 干净站点 → 0 条
- 检索类爬虫被拦 / 无 SSR / 全站无日期 → 各类各 1 条
- sameAs 为空但 Organization 存在 → 不算阻塞（与评分卡口径一致）
- 实体声明丰富度问题归 High：仅 BreadcrumbList / 全站无 Organization /
  主页缺 Organization（子页有）→ 均不升 P0
- 根因抑制：fetch 失败短路 ②④⑤；no_ssr 抑制 thin/zero_jsonld/no_date
- 封闭白名单：非白名单 consistency Critical 不升 P0
- 样本量门槛：全站无日期 <3 页不判
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


def _dated_pages(n, with_date=True):
    fr = {"best_date": "2026-01-01"} if with_date else {}
    return {"pages": [{"status_code": 200, "freshness": fr} for _ in range(n)],
            "product_pages": []}


def _run(fetch=None, bots=None, schema=None, issues=None, articles=None, robots=None):
    return compute_p0_blockers(
        fetch=fetch if fetch is not None else _fetch(),
        bots=bots if bots is not None else _bots(),
        schema=schema if schema is not None
        else {"has_organization": True, "sampled": {"homepage": 1, "article_pages": 3}},
        issues=issues if issues is not None else {},
        articles=articles if articles is not None else {},
        robots=robots)


# ── 基线场景 ─────────────────────────────────────────────────

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
    articles = _dated_pages(2)
    out = _run(fetch=_fetch(structured_data=[]), issues=issues, articles=articles)
    keys = {b["key"] for b in out}
    assert keys == {"homepage_zero_jsonld", "consistency:author_mismatch_template"}
    note = p0_blocking_note(out)
    assert "2 项 P0 级阻塞" in note and "可进行 GEO 优化" not in note


def test_homepage_zero_jsonld_blocking():
    """首页零 JSON-LD（机器侧完全空白）→ P0。"""
    out = _run(fetch=_fetch(structured_data=[]))
    assert [b["key"] for b in out] == ["homepage_zero_jsonld"]


def test_sameas_empty_not_blocking_when_org_exists():
    """sameAs 空但 Organization 存在 → 非阻塞（评分卡边界口径）。"""
    issues = _issues("entity_graph", "organization_no_sameas", "Critical")
    assert _run(issues=issues) == []


def test_sameas_empty_is_high_not_p0_in_analyzers():
    """回归：entity_graph 对「有 Organization 但 sameAs 空」定 High，不定 Critical。

    以前定 Critical，导致报告横幅（数 Critical finding 标红）与前置判定文案
    （按 blockers.py 口径说「可进行 GEO 优化」）打架——creativehit.ai 报告即此。
    """
    from diagnose.analyzers.entity_graph import analyze

    signals = {
        "main_page": {
            "meta": {"url": {"value": "https://a.com/"}},
            "entity": {"organization": {"value": {"@type": "Organization",
                                                  "name": "A", "url": "https://a.com/"}}},
        },
        "global": {"entities": {
            "organizations": [{"@type": "Organization", "name": "A",
                               "url": "https://a.com/", "page_url": "https://a.com/",
                               "sameAs": [], "foundingDate": "2020",
                               "address": {}, "contactPoint": {}}],
            "persons": [], "products": [], "articles": [],
        }},
    }
    by_kind = {i["kind"]: i for i in analyze(signals)}
    assert by_kind["organization_no_sameas"]["severity"] == "High"


# ── P0 口径校准：横幅与清单必须一致（cap_severity_p0）────────────

def test_cap_demotes_sameas_critical():
    """agent 把 sameAs 空写成 Critical → 降为 High（不能出现在 P0 清单）。"""
    from diagnose.blockers import cap_severity_p0
    assert cap_severity_p0("Critical", "organization_no_sameas") == "High"
    assert cap_severity_p0("Critical", "") == "High"


def test_cap_keeps_deterministic_blockers():
    """确定性阻塞保持 Critical——它们本来就会让横幅判红。"""
    from diagnose.blockers import cap_severity_p0
    for kind in ("fetch_failed", "no_ssr", "retrieval_bots_blocked",
                 "homepage_zero_jsonld", "author_mismatch_template",
                 "robots_disallow_all"):
        assert cap_severity_p0("Critical", kind) == "Critical"


def test_cap_demotes_inferred_blockers():
    """推断类阻塞（横幅只算「疑似」）不进 P0 清单，否则横幅说「可进行」清单却标红。"""
    from diagnose.blockers import cap_severity_p0
    for kind in ("thin_content", "no_heading_structure",
                 "no_machine_readable_date", "challenge_page"):
        assert cap_severity_p0("Critical", kind) == "High"


def test_cap_leaves_high_untouched():
    from diagnose.blockers import cap_severity_p0
    assert cap_severity_p0("High", "anything") == "High"
    assert cap_severity_p0("Medium", "organization_no_sameas") == "Medium"


# ── 实体声明丰富度问题 → High 非 P0（P0 锚在引用失败，不在形式缺失）────

def test_breadcrumb_only_homepage_not_blocking():
    """首页仅 BreadcrumbList、无 Organization/WebSite/Product → 非阻塞。

    管道已建立（JSON-LD 可读），缺的只是实体声明完整度；AI 可从正文/
    外链/知识图谱多源识别品牌实体，提及率可以正常——归 High 优化项。
    """
    fetch = _fetch(structured_data=[{"@type": "BreadcrumbList"}])
    schema = {"has_organization": False, "sampled": {"homepage": 1, "article_pages": 3}}
    assert _run(fetch=fetch, schema=schema) == []


def test_no_organization_site_not_blocking():
    """全站无 Organization → High，非 P0（削弱实体消歧，不切断引用通路）。"""
    schema = {"has_organization": False, "sampled": {"homepage": 1, "article_pages": 3}}
    assert _run(schema=schema) == []


def test_homepage_missing_organization_issue_not_blocking():
    """主页缺 Organization（子页有）→ entity_graph 报 Critical，但归 High。"""
    issues = _issues("entity_graph", "homepage_missing_organization", "Critical")
    assert _run(issues=issues) == []


# ── ① 抓取通道 ───────────────────────────────────────────────

def test_retrieval_bots_blocked():
    out = _run(bots=_bots(blocked={"GPTBot", "OAI-SearchBot"}))
    assert [b["key"] for b in out] == ["retrieval_bots_blocked"]
    assert "GPTBot" in out[0]["evidence"]
    assert out[0]["confidence"] == "deterministic"


def test_training_bot_blocked_not_blocking():
    """训练类爬虫被拦不切断检索通路 → 非阻塞。"""
    assert _run(bots=_bots(blocked={"ClaudeBot"})) == []


def test_no_ssr_blocking():
    out = _run(fetch=_fetch(has_ssr_content=False, structured_data=[{"@type": "Organization"}]))
    assert any(b["key"] == "no_ssr" for b in out)


def test_fetch_failure_blocking():
    out = _run(fetch={"error": "连接超时"})
    assert any(b["key"] == "fetch_failed" for b in out)


def test_missing_inputs_no_false_positive():
    """输入缺失/报错时不误判（fetch/bots/schema 为 error dict）。"""
    out = compute_p0_blockers(fetch={"error": "x"}, bots={"error": "x"},
                              schema={"error": "x"}, issues={}, articles={})
    assert [b["key"] for b in out] == ["fetch_failed"]


# ── ② 读不出内容 ─────────────────────────────────────────────

def test_thin_content_blocking():
    out = _run(fetch=_fetch(word_count=30))
    assert any(b["key"] == "thin_content" for b in out)
    # 有实质正文但可引用性低 → 非阻塞（词数不看 citability 分数）
    assert _run(fetch=_fetch(word_count=400)) == []


def test_thin_content_boundary():
    """阈值边界：49 判 / 50 不判；150 无标题判 / 150 有标题不判。"""
    assert any(b["key"] == "thin_content"
               for b in _run(fetch=_fetch(word_count=49)))
    assert _run(fetch=_fetch(word_count=50,
                             heading_structure=[{"level": 1}],
                             h1_tags=["x"])) == []
    out = _run(fetch=_fetch(word_count=100, heading_structure=[], h1_tags=[]))
    assert [b["key"] for b in out] == ["no_heading_structure"]
    assert _run(fetch=_fetch(word_count=100)) == []


# ── ③ consistency 白名单（原则 1：封闭枚举）───────────────────

def test_consistency_non_whitelist_kind_not_blocking():
    """非白名单 consistency Critical 不升 P0（未来新增 kind 默认只算 High）。"""
    issues = _issues("consistency", "some_future_critical_kind", "Critical")
    assert _run(issues=issues) == []


def test_consistency_whitelist_kind_blocking():
    issues = _issues("consistency", "author_mismatch_template", "Critical")
    out = _run(issues=issues)
    assert [b["key"] for b in out] == ["consistency:author_mismatch_template"]


# ── ⑤ 时效性 + 样本量门槛 ────────────────────────────────────

def test_all_pages_no_dates_blocking():
    """≥3 个有效采样页全无日期 → 判。"""
    art = _dated_pages(2, with_date=False)
    out = _run(fetch=_fetch(freshness={}), articles=art)
    assert any(b["key"] == "no_machine_readable_date" for b in out)


def test_no_dates_insufficient_samples_not_blocking():
    """有效采样页 <3 → 样本不足，不判（防文章列表抓取失败时误报）。"""
    assert _run(fetch=_fetch(freshness={})) == []
    art = _dated_pages(1, with_date=False)
    assert _run(fetch=_fetch(freshness={}), articles=art) == []


def test_articles_error_skips_freshness():
    """articles 报错 → 只剩首页 1 个样本 → 不判（上游缺失安全跳过）。"""
    out = _run(fetch=_fetch(freshness={}), articles={"error": "timeout"})
    assert out == []


# ── 根因抑制链（原则 2）──────────────────────────────────────

def test_fetch_failed_short_circuits_others():
    """fetch 失败 → ②④⑤ 短路，只报抓取类。"""
    issues = _issues("entity_graph", "homepage_missing_organization", "Critical")
    out = _run(fetch={"error": "连接超时"},
               schema={"has_organization": False, "sampled": {"homepage": 1, "article_pages": 5}},
               issues=issues)
    keys = {b["key"] for b in out}
    assert "fetch_failed" in keys
    assert not any(k.startswith(("thin", "homepage_zero", "no_machine")) for k in keys)


def test_no_ssr_suppresses_downstream():
    """no_ssr 命中 → thin_content / homepage_zero_jsonld / no_machine_readable_date 同根因抑制。"""
    art = _dated_pages(3, with_date=False)
    out = _run(fetch=_fetch(has_ssr_content=False, word_count=10,
                            structured_data=[], freshness={}),
               articles=art)
    keys = {b["key"] for b in out}
    assert keys == {"no_ssr"}
