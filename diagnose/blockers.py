"""P0 阻塞判定 — 五类 GEO 阻塞的确定性计算（规则同源的唯一来源）。

背景：阻塞判定曾分散在三处——评分 LLM 的 Critical 五类、执行清单 LLM 的
前置判定、渲染层数 Critical finding 的横幅——LLM 定级每次会漂，导致同一
次诊断里报告写「可直接优化」而清单写「存在阻塞」。

本模块把五类阻塞从实测数据（fetch/bots/schema/issues/articles）直接算出，
报告横幅、执行清单前置判定、评分卡 Critical 对齐都消费同一份结果：

  ① 抓取通道被切断：首页抓取失败 / 检索类爬虫被拦 / 挑战页 / 无 SSR
  ② 抓到但读不出内容：正文极薄 / 正文薄且无任何标题层级
  ③ 读懂了但不敢引用：consistency 分析器判 Critical（身份数据自相矛盾）
  ④ 实体锚定断裂：仅「首页零 JSON-LD」（机器侧完全空白）
  ⑤ 时效性准阻塞：全站无任何机器可读日期

P0 判定锚在「可观测的引用失败」上，而不是「形式缺失」上：
形式缺失（没有某类 schema / 字段）默认 High，除非它与引用失败有必然
因果（如无 SSR → 爬虫必然读不到正文）。首页零 JSON-LD 算 P0，因为
「零」意味着机器读取管道完全未建立；而首页有 JSON-LD（哪怕只有
BreadcrumbList）但无 Organization/WebSite/Product，说明管道在、内容在
——缺的只是实体声明的丰富度，归 High 优化项，不是阻塞。同理，
「全站无 Organization」削弱实体消歧的确定性但不切断引用通路（AI 可
从正文/外链/知识图谱多源识别品牌实体），也归 High。

规则设计六原则（新增规则必须逐条自查）：

  1. 门槛：P0 = 不修就无法被引用。宁漏勿判；拿不准的一律降级 High。
     「信号缺失」（上游没数据）与「信号显式为零」（扫了、确实没有）必须区分，
     前者跳过而非判零。能触发 P0 的 issue kind 用白名单封闭枚举，
     新 kind 默认只算 High，人工评审后才加入。
  2. 根因抑制：一个病根只报一条。fetch 失败时 ②④⑤ 短路；no_ssr 抑制
     thin_content / homepage_zero_jsonld / no_machine_readable_date
     （无 SSR 必然薄、必然零 JSON-LD、必然无日期——同根因不重复列）。
  3. 证据分级：直检（deterministic：HTTP 状态、JSON-LD 计数、爬虫探测）
     命中即判；推断（inferred：正文薄、全站无日期）需多信号佐证，
     evidence 中必须注明依据与余量。
  4. 抗抖动：阈值边界附近的命中在 evidence 写明具体数值；挑战页等
     间歇性信号标注 confidence 供报告端软化措辞。
  5. 口径一致：与评分卡互锁——分析器定 Critical 不等于阻塞
     （如 sameAs 为空：entity_graph 定 Critical，这里按评分卡口径排除）。
     每条规则的排除边界写在 RULE_EXCLUSIONS，评审时逐条核对。
  6. 回归锁定：每类规则在 tests/test_blockers.py 有命中 / 边界 / 抑制
     三组用例，历史误判场景沉淀为 fixture 测试。

注意边界（与评分卡一致，防止过判）：
  - 「有实质正文但可引用性低」是内容质量问题，不是阻塞——不看 citability 分数。
  - 首页有 JSON-LD 但缺 Organization/WebSite/Product（如仅 BreadcrumbList）：
    实体声明丰富度问题，归 High——管道已建立，缺的只是声明完整度。
  - 全站无 Organization / 主页缺 Organization：削弱实体消歧确定性，归 High。
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

# ③类中允许升级为阻塞的 consistency issue kind（白名单封闭枚举，原则 1）。
# 目前 consistency 分析器产出 Critical 的只有 author_mismatch_template
# （作者署名系统性冲突）；其余 kind 最高 High，天然不升 P0。
# 新增 kind 需人工评审确认「不修就不敢引用」后才可加入。
_CONSISTENCY_BLOCKER_KINDS = {"author_mismatch_template"}

# ⑤类最小样本量：有效页面数低于此值不做「全站无日期」推断（原则 3，防样本偏差）
_FRESHNESS_MIN_SAMPLES = 3

# 允许以 Critical 进入 P0 清单的 issue kind 白名单——与 compute_p0_blockers
# 中「确定性（deterministic）」的那几条一一对应，封闭枚举（原则 1）。
# cap_severity_p0 用它把上游写成 Critical 的非阻塞项降为 High。
#
# 只收确定性项：推断类（thin_content / no_heading_structure /
# no_machine_readable_date / challenge_page）在 p0_blocking_note 里只算
# 「疑似阻塞」，横幅会写「可进行 GEO 优化」——若它们仍以 P0 出现在清单里，
# 报告就会再次自相矛盾。推断项统一降为 High，等复测确认。
#
# 注意：sameAs 为空 / 主页缺 Organization / 全站无 Organization 均不在其中
# （entity_graph 已按评分卡口径定 High）。
_P0_WHITELIST = {
    "fetch_failed", "no_ssr", "retrieval_bots_blocked",
    "homepage_zero_jsonld", "author_mismatch_template",
    "robots_disallow_all",
}

# 每条规则的排除边界（原则 5：评审时逐条核对「什么时候不该触发」）。

# key 为 blocker key，value 为该规则明确排除、不升 P0 的情形。
RULE_EXCLUSIONS = {
    "fetch_failed": ["DNS/网络瞬时抖动由上层重试吸收；这里只认最终抓取结果"],
    "challenge_page": ["间歇性挑战（偶发拦一次）不算——evidence 标注 inferred，报告端软化措辞"],
    "no_ssr": ["has_ssr_content 字段缺失（未探测）不算"],
    "retrieval_bots_blocked": ["训练类爬虫被拦不算（不切断检索通路）"],
    "robots_disallow_all": ["robots.txt 不存在（404）或无法解析不算——无法确认配置意图"],
    "thin_content": ["有实质正文但可引用性低不算（内容质量，High）",
                     "no_ssr 命中时抑制（同根因）"],
    "no_heading_structure": ["词数 ≥150 不算；no_ssr 命中时抑制"],
    "consistency:*": ["非白名单 kind 一律 High，不升 P0（原则 1）"],
    "homepage_zero_jsonld": ["no_ssr 命中时抑制（同根因：无 SSR 必然零 JSON-LD）"],
    "no_machine_readable_date": ["有效采样页 <3 不算；no_ssr 命中时抑制"],
    # 以下情形曾误判为 P0，现明确归 High（实体声明丰富度问题，不切断引用通路）：
    # - 首页有 JSON-LD 但只有 BreadcrumbList 等导航类，无 Organization/WebSite/Product
    # - 全站无 Organization（AI 可从正文/外链/知识图谱多源识别品牌实体）
    # - 主页缺 Organization 但子页有（权重分散，entity_graph 仍报 Critical，归 High）
}


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


def _blocker(key: str, class_key: str, class_name: str, title: str,
             evidence: str, confidence: str) -> dict:
    """confidence: deterministic（直检命中即判）| inferred（推断，报告端软化措辞）。"""
    assert confidence in ("deterministic", "inferred")
    return {"key": key, "class_key": class_key, "class_name": class_name,
            "title": title, "evidence": evidence, "confidence": confidence}


def compute_p0_blockers(*, fetch: dict, bots: dict, schema: dict,
                        issues: dict, articles: dict, robots: dict = None) -> list:
    """从实测数据计算 P0 阻塞清单。输入缺失/报错时对应类别跳过（不误判）。

    抑制链（原则 2）：fetch 失败 → ②④⑤ 短路；no_ssr → 抑制
    thin_content / homepage_zero_jsonld / no_machine_readable_date。
    """
    blockers = []

    # ── ① 抓取通道被切断 ─────────────────────────────────────
    fetch_failed = False
    if isinstance(fetch, dict) and fetch.get("error"):
        fetch_failed = True
        blockers.append(_blocker(
            "fetch_failed", "access", "抓取通道被切断",
            "首页抓取失败",
            f"fetch 返回错误：{fetch.get('error')}",
            "deterministic"))
    elif isinstance(fetch, dict) and fetch.get("status_code") is None \
            and fetch.get("errors"):
        # 历史数据形状：fetch_page 早期只把异常写进 errors 列表、不写顶层
        # error。若不识别，报告会显示「可进行 GEO 优化」+ 30 分堪忧（2026-11 修）。
        fetch_failed = True
        blockers.append(_blocker(
            "fetch_failed", "access", "抓取通道被切断",
            "首页抓取失败",
            f"fetch 未取得响应：{fetch.get('errors')[-1]}",
            "deterministic"))
    elif isinstance(fetch, dict) and isinstance(fetch.get("status_code"), int) \
            and fetch["status_code"] >= 400:
        fetch_failed = True
        blockers.append(_blocker(
            "fetch_failed", "access", "抓取通道被切断",
            f"首页返回 HTTP {fetch['status_code']}",
            f"首页 {fetch.get('url') or ''} 状态码 {fetch['status_code']}",
            "deterministic"))

    challenge = _ok(fetch) and fetch.get("challenge_detected")
    if challenge:
        blockers.append(_blocker(
            "challenge_page", "access", "抓取通道被切断",
            "首页返回人机验证/挑战页",
            "challenge_detected=true，正文被验证页替代"
            "（单次探测结果，可能为间歇性挑战，建议复测确认）",
            "inferred"))

    no_ssr = _ok(fetch) and fetch.get("has_ssr_content") is False
    if no_ssr:
        wc = fetch.get("word_count") or 0
        blockers.append(_blocker(
            "no_ssr", "access", "抓取通道被切断",
            "首页无 SSR 内容",
            f"服务端渲染后正文仍为空（word_count={wc}），"
            "AI 爬虫（不执行 JS）读不到正文",
            "deterministic"))

    # ── ① 抓取通道被切断：声明层（robots.txt）+ 实测层（UA 重放）────────
    # 声明层：robots.txt 全禁（Disallow: / 通配）——无歧义的配置错误。
    # 即使实测放行（爬虫当前忽略 robots 继续抓），声明意图就是「不让爬」，
    # 一旦 OpenAI/Anthropic 收紧 robots 遵从策略，引用瞬间断供。
    # 按「声明或实测任一断供即 P0」的统一口径处理（2026-11-19 定）。
    robots_disallow_all = False
    robots_blocked_names = []
    if _ok(robots) and robots.get("exists"):
        for bot, status in (robots.get("ai_crawler_status") or {}).items():
            if status in ("BLOCKED", "BLOCKED_BY_WILDCARD"):
                robots_blocked_names.append(bot)
        if robots_blocked_names:
            robots_disallow_all = True

    # 实测层：UA 重放真的被拦
    blocked_retrieval = []
    if _ok(bots):
        blocked_retrieval = [p.get("bot") for p in bots.get("probes") or []
                             if isinstance(p, dict) and p.get("blocked")
                             and p.get("class") in _RETRIEVAL_CLASSES and p.get("bot")]

    if robots_disallow_all and blocked_retrieval:
        # 声明与实测都断供：合并为一条，证据双倍确凿（原则 2：同根因只报一条）
        blockers.append(_blocker(
            "retrieval_bots_blocked", "access", "抓取通道被切断",
            f"AI 爬虫被 robots.txt 全禁且实测确认被拦（{len(blocked_retrieval)} 个检索类）",
            f"robots.txt 声明层全禁（{len(robots_blocked_names)} 个爬虫 BLOCKED/BLOCKED_BY_WILDCARD），"
            f"UA 重放实测同样被拦：{'、'.join(blocked_retrieval)}——声明与实测双重断供",
            "deterministic"))
    elif robots_disallow_all:
        # 声明全禁但实测放行：配置错误，政策收紧即断供
        blockers.append(_blocker(
            "robots_disallow_all", "access", "抓取通道被切断",
            "robots.txt 全站 Disallow: /，声明层全禁（实测暂未生效）",
            f"robots.txt 对 {len(robots_blocked_names)} 个 AI/搜索爬虫声明全禁"
            f"（{'、'.join(robots_blocked_names[:4])}{' 等' if len(robots_blocked_names) > 4 else ''}），"
            "UA 重放实测仍能抓取——爬虫当前忽略 robots 继续抓，"
            "但配置意图与现状矛盾，一旦爬虫政策收紧，引用通路即刻断供",
            "deterministic"))
    elif blocked_retrieval:
        blockers.append(_blocker(
            "retrieval_bots_blocked", "access", "抓取通道被切断",
            f"检索类 AI 爬虫被拦（{len(blocked_retrieval)} 个）",
            "被拦：" + "、".join(blocked_retrieval),
            "deterministic"))

    # fetch 失败时 ②④⑤ 均无从谈起——短路，只报抓取类（原则 2）
    if not fetch_failed:

        # ── ② 抓到了但读不出内容（no_ssr 命中时抑制：无 SSR 必然薄）──
        if _fetch_ok(fetch) and not no_ssr:
            wc = fetch.get("word_count") or 0
            has_headings = bool(fetch.get("heading_structure")) or bool(fetch.get("h1_tags"))
            if wc < _THIN_WORDS:
                blockers.append(_blocker(
                    "thin_content", "readability", "抓到但读不出内容",
                    "首页正文极薄",
                    f"首页 word_count={wc}（阈值 <{_THIN_WORDS}），无实质正文可供引用",
                    "inferred"))
            elif wc < _THIN_WORDS_WITH_NO_HEADINGS and not has_headings:
                blockers.append(_blocker(
                    "no_heading_structure", "readability", "抓到但读不出内容",
                    "首页正文薄且无任何标题层级",
                    f"首页 word_count={wc}（阈值 <{_THIN_WORDS_WITH_NO_HEADINGS}）、"
                    "heading_structure 与 h1 均为空",
                    "inferred"))

        # ── ③ 读懂了但不敢引用（consistency 白名单 Critical）────────
        for it in _flat_issues(issues):
            if it.get("category") == "consistency" \
                    and it.get("severity") == "Critical" \
                    and it.get("kind") in _CONSISTENCY_BLOCKER_KINDS:
                blockers.append(_blocker(
                    f"consistency:{it.get('kind')}", "trust", "读懂了但不敢引用",
                    it.get("title") or "身份数据自相矛盾",
                    str((it.get("evidence") or {}))[:300],
                    "deterministic"))

        # ── ④ 实体锚定断裂：仅「首页零 JSON-LD」（机器侧完全空白）────
        # 有 JSON-LD 但缺 Organization/WebSite/Product 是实体声明丰富度
        # 问题（High），不切断引用通路——AI 可从正文/外链多源识别品牌实体。
        if _fetch_ok(fetch) and not no_ssr:
            homepage_sd = fetch.get("structured_data")
            if isinstance(homepage_sd, list) and len(homepage_sd) == 0:
                blockers.append(_blocker(
                    "homepage_zero_jsonld", "entity", "实体锚定断裂",
                    "首页零 JSON-LD，品牌官方声明页在机器侧空白",
                    "首页抽取到 0 个结构化数据块；AI 建品牌实体优先读首页",
                    "deterministic"))

        # ── ⑤ 时效性准阻塞：全站无机器可读日期 ─────────────────────
        # no_ssr 命中时抑制（无 SSR 必然无日期）；样本 <3 页不判（原则 3）
        if not no_ssr:
            pages = []
            if _fetch_ok(fetch):
                pages.append(fetch)
            if _ok(articles):
                pages += [p for p in (articles.get("pages") or []) if _fetch_ok(p)]
                pages += [p for p in (articles.get("product_pages") or []) if _fetch_ok(p)]
            if len(pages) >= _FRESHNESS_MIN_SAMPLES:
                def _has_date(p):
                    fr = p.get("freshness") or {}
                    return bool(fr.get("best_date") or fr.get("date_modified") or fr.get("date_published"))
                if not any(_has_date(p) for p in pages):
                    blockers.append(_blocker(
                        "no_machine_readable_date", "freshness", "时效性准阻塞",
                        "全站无机器可读日期",
                        f"{len(pages)} 个采样页面均无 dateModified/<time>/Last-Modified",
                        "inferred"))

    # 按 key 去重（同 key 可能被直检与 issue 双路命中）
    seen, out = set(), []
    for b in blockers:
        if b["key"] not in seen:
            seen.add(b["key"])
            out.append(b)
    return out


def p0_blocking_note(blockers: list) -> str:
    """报告/清单共用的前置判定文案。blockers=None 表示无数据（走旧兜底）。

    只有确定性（deterministic）阻塞才进入「不可直接优化」文案；
    推断类（inferred）降级为「疑似」提示，不阻断、不定 P0（原则 3/4：
    推断需多信号佐证，拿不准的一律降级）。inferred 项单独列出供人工复核。
    """
    if not blockers:
        return "可进行 GEO 优化，以下内容可慢慢调整。"

    confirmed = [b for b in blockers if b.get("confidence") != "inferred"]
    suspected = [b for b in blockers if b.get("confidence") == "inferred"]

    if confirmed:
        titles = "；".join(b["title"] for b in confirmed[:3])
        more = f" 等 {len(confirmed)} 项" if len(confirmed) > 3 else ""
        note = (f"当前官网不可直接 GEO 优化，包含 {len(confirmed)} 项 P0 级阻塞需前置修复："
                f"{titles}{more}；在这些问题修复之前，其余优化动作难以生效。")
        if suspected:
            s_titles = "；".join(b["title"] for b in suspected[:2])
            note += f" 另有 {len(suspected)} 项疑似阻塞（{s_titles}），建议复测确认。"
        return note

    # 仅有疑似项：不阻断，提示复测
    s_titles = "；".join(b["title"] for b in suspected[:3])
    more = f" 等 {len(suspected)} 项" if len(suspected) > 3 else ""
    return (f"可进行 GEO 优化。另有 {len(suspected)} 项疑似阻塞待确认："
            f"{s_titles}{more}——建议复测后再定优先级。")


def cap_severity_p0(severity: str, kind: str, blockers: "list | None" = None) -> str:
    """把 P0 白名单之外的问题从 Critical 降到 High（评分卡口径的唯一出口）。

    背景：agent 的 scores.json 与分析器都可以写 Critical，而报告横幅、
    P0 清单、执行计划前置判定三处口径必须一致——`compute_p0_blockers`
    的规则是唯一裁判。凡是它明确排除、不升 P0 的情形（RULE_EXCLUSIONS），
    即使上游写成 Critical，也不能以「P0 立即修复」出现在行动清单里，
    否则同一份报告会出现「可进行 GEO 优化」横幅 + 红色 P0 清单（
    creativehit.ai 报告即此）。

    「唯一裁判」的落地方式（2026-11 修）：传了 blockers 时，只有**实际
    命中**的确定性 blocker key 才放行 Critical——而不是查白名单常量。
    历史问题：旧实现接受 agent 自报的 kind（`f.get("kind")`）并且用标题
    正则猜 kind，两处都可绕过：
      · 标题含「结构化数据」的产品页 schema 问题被猜成 homepage_zero_jsonld
        → 横幅写「可动工」却出现 P0 卡；
      · agent 自写 kind="no_ssr"（站点 SSR 正常）同样进 P0。
    现在 kind 只用于「映射到 blocker key」，最终裁判是 blockers 集合。

    未传 blockers（旧数据/调用方未算）时退回白名单常量，保持向后兼容。
    """
    if severity != "Critical":
        return severity
    allowed = _P0_WHITELIST
    if blockers is not None:
        allowed = {b.get("key") for b in blockers
                   if isinstance(b, dict) and b.get("confidence") != "inferred"}
    return severity if kind in allowed else "High"

