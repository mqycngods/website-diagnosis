"""robots.txt 声明层全禁 → P0 阻塞的回归测试（2026-11-19 定口径）。

背景（OceanPayment 报告事故）：
  robots.txt 全文「User-agent: * / Disallow: /」，12 个爬虫在声明层被
  一次性关掉；但 UA 重放实测全部 200——报告同时出现
  「robots 全禁」的 finding 与「可达性 100 分」，横幅却写「可进行 GEO 优化」。
  声明层与实测层各说各话，P0 判定完全忽略 robots。

定口径（与评分卡一致）：
  - 声明全禁 OR 实测断供，任一成立即 P0（deterministic）
  - 声明与实测都断供 → 合并为一条 retrieval_bots_blocked（同根因只报一条）
  - 声明全禁但实测放行 → robots_disallow_all（配置错误，政策收紧即断供）
  - 实测被拦但声明没禁 → retrieval_bots_blocked（WAF/CDN 层拦截）
  - robots.txt 不存在/无法解析 → 不判（无法确认配置意图）
"""
from diagnose.blockers import compute_p0_blockers


def _fetch():
    return {"url": "https://a.com", "status_code": 200, "word_count": 800,
            "has_ssr_content": True, "challenge_detected": False,
            "heading_structure": [{"level": 1, "text": "x"}], "h1_tags": ["x"],
            "structured_data": [{"@type": "Organization"}],
            "freshness": {"best_date": "2026-01-01"}}


def _bots(blocked=()):
    classes = {"GPTBot": "live-retrieval", "OAI-SearchBot": "search-index",
               "ClaudeBot": "training"}
    return {"probes": [{"bot": n, "class": c, "blocked": n in blocked}
                       for n, c in classes.items()]}


def _robots_disallow_all(names=("GPTBot", "ClaudeBot")):
    return {"exists": True,
            "ai_crawler_status": {n: "BLOCKED_BY_WILDCARD" for n in names}}


def _run(robots=None, bots=None):
    return compute_p0_blockers(
        fetch=_fetch(), bots=bots if bots is not None else _bots(),
        schema={"has_organization": True,
                "sampled": {"homepage": 1, "article_pages": 3}},
        issues={}, articles={}, robots=robots)


# ── 声明全禁 + 实测放行 → P0（robots_disallow_all）────────────

def test_disallow_all_with_open_probes_is_p0():
    """robots.txt 全禁但实测全通——配置错误，政策收紧即断供，必须 P0。"""
    out = _run(robots=_robots_disallow_all())
    keys = [b["key"] for b in out]
    assert "robots_disallow_all" in keys
    assert "retrieval_bots_blocked" not in keys   # 实测没被拦，不该出这条
    b = next(b for b in out if b["key"] == "robots_disallow_all")
    assert b["confidence"] == "deterministic"
    assert "实测暂未生效" in b["title"]


# ── 声明全禁 + 实测也断供 → 合并为一条 retrieval_bots_blocked ──

def test_disallow_all_with_blocked_probes_merges_into_one():
    out = _run(robots=_robots_disallow_all(),
               bots=_bots(blocked=("GPTBot",)))
    keys = [b["key"] for b in out]
    assert "retrieval_bots_blocked" in keys
    assert "robots_disallow_all" not in keys       # 同根因只报一条（原则 2）
    b = next(b for b in out if b["key"] == "retrieval_bots_blocked")
    assert "双重断供" in b["evidence"]
    assert "GPTBot" in b["evidence"]


# ── 实测断供但声明没禁 → retrieval_bots_blocked（原有行为不变）──

def test_blocked_probes_without_robots_still_p0():
    out = _run(robots={"exists": True, "ai_crawler_status": {}},
               bots=_bots(blocked=("OAI-SearchBot",)))
    keys = [b["key"] for b in out]
    assert "retrieval_bots_blocked" in keys
    assert "robots_disallow_all" not in keys


# ── 声明全禁但 robots 不存在 → 不判（无法确认配置意图）────────

def test_missing_robots_not_flagged():
    out = _run(robots={"exists": False, "ai_crawler_status": {}}, bots=_bots())
    assert all(b["key"] != "robots_disallow_all" for b in out)


def test_none_robots_not_flagged():
    """调用方没传 robots（旧数据/旧调用路径）→ 不判，保持向后兼容。"""
    out = _run(robots=None, bots=_bots())
    assert all(b["key"] != "robots_disallow_all" for b in out)


# ── 部分拦截（非全禁）→ 不判 robots_disallow_all ─────────────

def test_partial_block_not_disallow_all():
    robots = {"exists": True,
              "ai_crawler_status": {"GPTBot": "PARTIALLY_BLOCKED",
                                    "ClaudeBot": "ALLOWED_BY_DEFAULT"}}
    out = _run(robots=robots, bots=_bots())
    assert all(b["key"] != "robots_disallow_all" for b in out)


# ── robots_disallow_all 进白名单：对应 finding 可保留 Critical ──

def test_disallow_all_kind_survives_cap():
    from diagnose.blockers import cap_severity_p0
    blockers = [{"key": "robots_disallow_all", "confidence": "deterministic"}]
    assert cap_severity_p0("Critical", "robots_disallow_all", blockers) == "Critical"


# ── 可达性评分封顶：robots 全禁时实测分不得超过 60 ───────────

def test_robots_cap_on_overall_score():
    from diagnose.pipeline import _apply_robots_cap
    bots = {"overall_score": 100, "probes": []}
    out = _apply_robots_cap(bots, _robots_disallow_all())
    assert out["overall_score"] == 60
    assert out["robots_cap_applied"] is True
    # 封顶必须给人类可读原因——报告里不能只显示 60 分却不解释为什么
    assert "robots_cap_reason" in out
    assert "封顶" in out["robots_cap_reason"]
    assert "GPTBot" in out["robots_cap_reason"] or "ClaudeBot" in out["robots_cap_reason"]


def test_no_cap_when_robots_clean():
    from diagnose.pipeline import _apply_robots_cap
    bots = {"overall_score": 100, "probes": []}
    clean = {"exists": True,
             "ai_crawler_status": {"GPTBot": "ALLOWED_BY_DEFAULT"}}
    out = _apply_robots_cap(bots, clean)
    assert out["overall_score"] == 100
    assert "robots_cap_applied" not in out


def test_no_cap_when_robots_missing():
    from diagnose.pipeline import _apply_robots_cap
    bots = {"overall_score": 100, "probes": []}
    out = _apply_robots_cap(bots, {"exists": False})
    assert out["overall_score"] == 100
