"""P0 横幅与行动清单口径一致性的回归测试。

背景（2026-11 creativehit.ai / OceanPayment 报告事故）：
  - 横幅写「包含 1 项 P0 级阻塞需前置修复：首页无 SSR 内容」+「查看 P0 清单」锚点
  - 行动清单 P0 桶为空，#p0-tier 锚点失效
  根因两条：
  ① _finding_kind 的 SSR 正则太窄（「依赖 JS 渲染」「服务端返回」不匹配）
  ② action-plan.html.j2 只在 P0 桶非空时写 id="p0-tier"
本测试同时锁定两条修复。
"""
import re

from diagnose.blockers import cap_severity_p0


def _finding_kind_local(title: str) -> str:
    """与 render/build.py 中 _finding_kind 的 SSR/robots 分支保持一致的本地实现，
    用于独立验证正则覆盖面（不拉起完整 build 管线）。"""
    t = (title or "").lower()
    if re.search(r"无\s*ssr|服务端渲染|空壳|仅.*js|依赖\s*js|js\s*渲染|客户端渲染|csr|javascript\s*渲染|服务端\s*返回|服务端\s*无\s*正文|正文\s*完全\s*依赖", t, re.I):
        return "no_ssr"
    if "robots" in t and re.search(r"disallow|全站|全禁|全部|通配|所有爬虫|声明.*禁", t):
        return "robots_disallow_all"
    if re.search(r"爬虫|gptbot|claudebot|ai bot|机器人", t) and \
            re.search(r"拦|禁|block|拒|403|disallow", t):
        return "retrieval_bots_blocked"
    return ""


# ── ① SSR 正则覆盖面：agent 各种写法都能映射到 no_ssr ──────

def test_ssr_regex_matches_js_dependency_wording():
    assert _finding_kind_local("首页正文完全依赖 JS 渲染，服务端返回的 108 词全是占位提示") == "no_ssr"


def test_ssr_regex_matches_plain_no_ssr():
    assert _finding_kind_local("首页无 SSR 内容") == "no_ssr"


def test_ssr_regex_matches_server_side_return():
    assert _finding_kind_local("服务端返回 108 词全是占位提示") == "no_ssr"


def test_ssr_regex_matches_client_side_rendering():
    assert _finding_kind_local("客户端渲染导致正文不可见") == "no_ssr"


def test_ssr_regex_does_not_match_unrelated_thin_content():
    """「正文薄」本身不是 SSR 问题，不能误映射。"""
    assert _finding_kind_local("首页正文薄但非 JS 问题") == ""


# ── ① 口径：命中 no_ssr 的 finding 在 blockers 命中时保留 Critical ──

def test_cap_p0_keeps_critical_when_no_ssr_blocker_active():
    blockers = [{"key": "no_ssr", "confidence": "deterministic"}]
    assert cap_severity_p0("Critical", "no_ssr", blockers) == "Critical"


def test_cap_p0_downgrades_when_blocker_not_active():
    """blockers 里没有 no_ssr 时，自报 Critical 必须降级 High。"""
    blockers = [{"key": "retrieval_bots_blocked", "confidence": "deterministic"}]
    assert cap_severity_p0("Critical", "no_ssr", blockers) == "High"


def test_cap_p0_inferred_blocker_does_not_keep_critical():
    """inferred 阻塞不升 P0——对应的 finding 也应降级。"""
    blockers = [{"key": "no_ssr", "confidence": "inferred"}]
    assert cap_severity_p0("Critical", "no_ssr", blockers) == "High"


# ── ② 模板：P0 桶为空时 #p0-tier 锚点依然存在 ──────────────

def test_p0_tier_anchor_present_even_when_empty():
    """渲染模板：P0 区块始终带 id="p0-tier"，空桶时渲染占位说明。"""
    tpl = open("templates/sections/action-plan.html.j2", encoding="utf-8").read()
    # P0 分支必须在 group 判断之外、无条件输出 id
    p0_section = re.search(r'\{%\s*if tier == "P0"\s*%\}(.+?)\{%\s*elif', tpl, re.S)
    assert p0_section, "模板里必须有独立的 P0 分支"
    body = p0_section.group(1)
    assert 'id="p0-tier"' in body, "P0 分支必须始终输出 id=p0-tier"
    assert "无 P0 级行动项" in body, "P0 空桶时必须有占位说明"


def test_banner_jumps_to_p0_tier_when_confirmed_blocking():
    """横幅「查看 P0 清单」只在 confirmed_p0 > 0 时显示，但锚点始终存在。"""
    tpl = open("templates/base.html.j2", encoding="utf-8").read()
    assert 'href="#p0-tier"' in tpl
    assert 'class="p0-gate-jump"' in tpl


# ── robots finding 的 kind 映射（2026-11-19 线上事故：robots 全禁 finding 被降 P1）──

def test_robots_disallow_all_title_maps_to_kind():
    """线上事故标题必须映射到 robots_disallow_all，否则 cap_p0 降为 High/P1。"""
    assert _finding_kind_local("robots.txt 对全部 12 个 AI 与搜索爬虫声明 Disallow: /") == "robots_disallow_all"


def test_robots_disallow_all_finding_keeps_critical():
    """robots 全禁 finding + blocker 命中 → 必须保留 Critical（进 P0 桶）。"""
    kind = _finding_kind_local("robots.txt 对全部 12 个 AI 与搜索爬虫声明 Disallow: /")
    blockers = [{"key": "robots_disallow_all", "confidence": "deterministic"}]
    assert cap_severity_p0("Critical", kind, blockers) == "Critical"


def test_retrieval_blocked_title_maps_to_blocked_kind():
    """实测被拦的 finding 仍映射到 retrieval_bots_blocked。"""
    assert _finding_kind_local("检索类 AI 爬虫被拦（3 个）") == "retrieval_bots_blocked"


# ── 加固：agent 自报 kind 直接放行，正则只作兜底（2026-11-19）──

def test_agent_reported_kind_takes_priority_over_title_regex():
    """agent 带 kind 的 finding 即使标题不匹配任何正则，也必须保留 Critical。

    加固核心：标题正则猜 kind 三次因措辞差异漏配（SSR 一次、robots 一次），
    agent 从 p0_blockers 复制的 kind 是唯一可信来源。
    """
    # 标题故意写得跟任何正则都不沾边
    title = "技术架构评审时发现的关键阻断项"
    # agent 自报 kind（从 blocker 复制）
    kind = "robots_disallow_all"
    blockers = [{"key": "robots_disallow_all", "confidence": "deterministic"}]
    assert cap_severity_p0("Critical", kind, blockers) == "Critical"


def test_agent_reported_kind_does_not_bypass_blocker_check():
    """自报 kind 不能凭空升 P0：blockers 里没有该 key 时仍降级。"""
    kind = "robots_disallow_all"
    blockers = [{"key": "no_ssr", "confidence": "deterministic"}]
    assert cap_severity_p0("Critical", kind, blockers) == "High"


def test_finding_model_carries_kind():
    """Finding dataclass 必须有 kind 字段（否则 build 层无法透传到 diagnostics）。"""
    from render.model import Finding
    import dataclasses
    fields = {f.name for f in dataclasses.fields(Finding)}
    assert "kind" in fields


def test_kind_flows_into_diagnostics_findings():
    """agent 自报 kind 必须穿透 build → diagnostics → findings（asdict），
    供后续优先级映射使用。用最小数据走完整 build 管线验证。"""
    import sys
    sys.path.insert(0, ".")
    from render.build import build_report_from_dict

    data = {
        "fetch": {"url": "https://x.com", "status_code": 200, "word_count": 800,
                  "has_ssr_content": True, "challenge_detected": False,
                  "heading_structure": [{"level": 1, "text": "x"}], "h1_tags": ["x"],
                  "structured_data": [{"@type": "Organization", "name": "X"}],
                  "freshness": {"best_date": "2026-01-01", "age_days": 30, "tier": "fresh"},
                  "meta_tags": {}, "title": "X"},
        "robots": {"exists": True, "ai_crawler_status": {"GPTBot": "BLOCKED_BY_WILDCARD"}},
        "bots": {"overall_score": 60, "probes": []},
        "citability": {"average_citability_score": 50},
        "schema": {"has_organization": True, "sameas_count": 0, "inventory": [],
                   "sampled": {"homepage": 1, "article_pages": 3}},
        "brand": {},
        "technical": {"security_score": 44, "headers_present": 0, "headers_total": 6,
                      "https": True, "security_matrix": [],
                      "llms": {"llms_txt": False, "llms_full_txt": False}},
        "scores": {
            "findings": [
                {"title": "robots.txt 声明全禁", "evidence": "x", "impact": "x",
                 "fix": "x", "confidence": "Confirmed", "severity": "Critical",
                 "layer": "domain", "kind": "robots_disallow_all"}
            ]
        },
        "articles": {}, "issues": {},
    }
    report = build_report_from_dict(data, domain="x.com", url="https://x.com",
                                    audit_date="2026-11-19")
    f = report.findings[0]
    # ① kind 穿透到 Finding
    assert f.kind == "robots_disallow_all"
    # ② 因为 blocker 命中（robots_disallow_all deterministic），severity 保 Critical
    assert f.severity == "Critical"
    # ③ 对应的 priority_action 进 P0 桶
    pa = [a for a in report.priority_actions if a.finding_index == 0]
    assert pa and pa[0].priority == "P0"


def test_ssr_finding_with_js_wording_survives_cap():
    """模拟线上事故：agent 写「依赖 JS 渲染」+ blockers 命中 no_ssr → 必须保留 Critical。"""
    title = "首页正文完全依赖 JS 渲染，服务端返回的 108 词全是占位提示"
    kind = _finding_kind_local(title)
    blockers = [{"key": "no_ssr", "confidence": "deterministic"}]
    assert cap_severity_p0("Critical", kind, blockers) == "Critical"
