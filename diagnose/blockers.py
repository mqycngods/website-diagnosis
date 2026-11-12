"""P0 阻塞判定 — 五类 GEO 阻塞的确定性计算（规则同源的唯一来源）。

背景：阻塞判定曾分散在三处——评分 LLM 的 Critical 五类、执行清单 LLM 的
前置判定、渲染层数 Critical finding 的横幅——LLM 定级每次会漂，导致同一
次诊断里报告写「可直接优化」而清单写「存在阻塞」。

本模块把五类阻塞从实测数据（fetch/bots/schema/issues/articles）直接算出，
报告横幅、执行清单前置判定、评分卡 Critical 对齐都消费同一份结果：

  ① 抓取通道被切断：首页抓取失败 / 检索类爬虫被拦 / 挑战页 / 无 SSR
  ② 抓到但读不出内容：正文极薄 / 正文薄且无任何标题层级
  ③ 读懂了但不敢引用：consistency 分析器判 Critical（身份数据自相矛盾）
  ④ 实体锚定断裂：首页零 JSON-LD / 全站无 Organization / 主页缺 Organization
  ⑤ 时效性准阻塞：全站无任何机器可读日期

注意边界（与评分卡一致，防止过判）：
  - 「有实质正文但可引用性低」是内容质量问题，不是阻塞——不看 citability 分数。
  - Organization 存在但 sameAs 为空：削弱图谱对齐但不切断引用，不算阻塞
    （entity_graph 分析器虽定 Critical，这里按评分卡口径排除）。
"""
from __future__ import annotations

# 检索类爬虫（拦住它们 = 切断 AI 搜索引用通路）
_RETRIEVAL_CLASSES = ("live-retrieval", "search-index")

# ②类阈值：正文词数低于此值视为「读不出内容」
_THIN_WORDS = 50
# ②类补充：词数低于此值且完全没有标题层级也算（营销短句堆砌页）
_THIN_WORDS_WITH_NO_HEADINGS = 150

# ④类中允许升级为阻塞的 entity_graph issue kind（sameAs 类明确排除）
_ENTITY_BLOCKER_KINDS = {"homepage_missing_organization"}

# ③类取 consistency 分析器的 Critical issue（作者/品牌身份系统性冲突等）


def _ok(d) -> bool:
    return isinstance(d, dict) and not d.get("error")


def _fetch_ok(fetch: dict) -> bool:
    return _ok(fetch) and isinstance(fetch.get("status_code"), int) \
        and 200 <= fetch["status_code"] < 400


def _flat_issues(issues: dict) -> list:
    """issues 是 {analyzer_name: [issue, ...]}，拍平成列表。"""
    out = []
    if isinstance(issues, dict):
        for lst in issues.values():
            if isinstance(lst, list):
                out.extend(i for i in lst if isinstance(i, dict))
    return out


def _blocker(key: str, class_key: str, class_name: str, title: str, evidence: str) -> dict:
    return {"key": key, "class_key": class_key, "class_name": class_name,
            "title": title, "evidence": evidence}


def compute_p0_blockers(*, fetch: dict, bots: dict, schema: dict,
                        issues: dict, articles: dict) -> list:
    """从实测数据计算 P0 阻塞清单。输入缺失/报错时对应类别跳过（不误判）。"""
    blockers = []

    # ── ① 抓取通道被切断 ─────────────────────────────────────
    if isinstance(fetch, dict) and fetch.get("error"):
        blockers.append(_blocker(
            "fetch_failed", "access", "抓取通道被切断",
            "首页抓取失败",
            f"fetch 返回错误：{fetch.get('error')}"))
    elif isinstance(fetch, dict) and isinstance(fetch.get("status_code"), int) \
            and fetch["status_code"] >= 400:
        blockers.append(_blocker(
            "fetch_failed", "access", "抓取通道被切断",
            f"首页返回 HTTP {fetch['status_code']}",
            f"首页 {fetch.get('url') or ''} 状态码 {fetch['status_code']}"))

    if _ok(fetch) and fetch.get("challenge_detected"):
        blockers.append(_blocker(
            "challenge_page", "access", "抓取通道被切断",
            "首页返回人机验证/挑战页",
            "challenge_detected=true，正文被验证页替代"))

    if _ok(fetch) and fetch.get("has_ssr_content") is False:
        blockers.append(_blocker(
            "no_ssr", "access", "抓取通道被切断",
            "首页无 SSR 内容",
            "服务端渲染后正文仍为空，AI 爬虫（不执行 JS）读不到正文"))

    if _ok(bots):
        blocked_retrieval = [p.get("bot") for p in bots.get("probes") or []
                             if isinstance(p, dict) and p.get("blocked")
                             and p.get("class") in _RETRIEVAL_CLASSES and p.get("bot")]
        if blocked_retrieval:
            blockers.append(_blocker(
                "retrieval_bots_blocked", "access", "抓取通道被切断",
                f"检索类 AI 爬虫被拦（{len(blocked_retrieval)} 个）",
                "被拦：" + "、".join(blocked_retrieval)))

    # ── ② 抓到了但读不出内容 ─────────────────────────────────
    if _fetch_ok(fetch):
        wc = fetch.get("word_count") or 0
        has_headings = bool(fetch.get("heading_structure")) or bool(fetch.get("h1_tags"))
        if wc < _THIN_WORDS:
            blockers.append(_blocker(
                "thin_content", "readability", "抓到但读不出内容",
                "首页正文极薄",
                f"首页 word_count={wc}（<{_THIN_WORDS}），无实质正文可供引用"))
        elif wc < _THIN_WORDS_WITH_NO_HEADINGS and not has_headings:
            blockers.append(_blocker(
                "no_heading_structure", "readability", "抓到但读不出内容",
                "首页正文薄且无任何标题层级",
                f"首页 word_count={wc}、heading_structure 与 h1 均为空"))

    # ── ③ 读懂了但不敢引用（consistency 分析器的 Critical）─────
    for it in _flat_issues(issues):
        if it.get("category") == "consistency" and it.get("severity") == "Critical":
            blockers.append(_blocker(
                f"consistency:{it.get('kind')}", "trust", "读懂了但不敢引用",
                it.get("title") or "身份数据自相矛盾",
                str((it.get("evidence") or {}))[:300]))

    # ── ④ 实体锚定断裂 ───────────────────────────────────────
    homepage_zero = False
    if _fetch_ok(fetch):
        homepage_sd = fetch.get("structured_data")
        if isinstance(homepage_sd, list) and len(homepage_sd) == 0:
            homepage_zero = True
            blockers.append(_blocker(
                "homepage_zero_jsonld", "entity", "实体锚定断裂",
                "首页零 JSON-LD，品牌官方声明页在机器侧空白",
                "首页抽取到 0 个结构化数据块；AI 建品牌实体优先读首页"))
    if _ok(schema) and schema.get("has_organization") is False:
        blockers.append(_blocker(
            "no_organization_site", "entity", "实体锚定断裂",
            "全站无 Organization",
            "所有采样页面均未检出 Organization/LocalBusiness"))
    for it in _flat_issues(issues):
        if it.get("category") == "entity_graph" and it.get("kind") in _ENTITY_BLOCKER_KINDS:
            # 首页零 JSON-LD 必然导致主页缺 Organization——同根因，不重复列
            if homepage_zero and it.get("kind") == "homepage_missing_organization":
                continue
            blockers.append(_blocker(
                f"entity_graph:{it.get('kind')}", "entity", "实体锚定断裂",
                it.get("title") or "主页缺 Organization",
                str((it.get("evidence") or {}))[:300]))

    # ── ⑤ 时效性准阻塞：全站无机器可读日期 ─────────────────────
    pages = []
    if _fetch_ok(fetch):
        pages.append(fetch)
    if _ok(articles):
        pages += [p for p in (articles.get("pages") or []) if _fetch_ok(p)]
        pages += [p for p in (articles.get("product_pages") or []) if _fetch_ok(p)]
    if pages:
        def _has_date(p):
            fr = p.get("freshness") or {}
            return bool(fr.get("best_date") or fr.get("date_modified") or fr.get("date_published"))
        if not any(_has_date(p) for p in pages):
            blockers.append(_blocker(
                "no_machine_readable_date", "freshness", "时效性准阻塞",
                "全站无机器可读日期",
                f"{len(pages)} 个采样页面均无 dateModified/<time>/Last-Modified"))

    # 按 key 去重（同 key 可能被直检与 issue 双路命中）
    seen, out = set(), []
    for b in blockers:
        if b["key"] not in seen:
            seen.add(b["key"])
            out.append(b)
    return out


def p0_blocking_note(blockers: list) -> str:
    """报告/清单共用的前置判定文案。blockers=None 表示无数据（走旧兜底）。"""
    if blockers:
        titles = "；".join(b["title"] for b in blockers[:3])
        more = f" 等 {len(blockers)} 项" if len(blockers) > 3 else ""
        return (f"当前官网不可直接 GEO 优化，包含 {len(blockers)} 项 P0 级阻塞需前置修复："
                f"{titles}{more}；在这些问题修复之前，其余优化动作难以生效。")
    return "可进行 GEO 优化，以下内容可慢慢调整。"
