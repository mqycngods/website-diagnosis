"""无状态诊断管道 — 方案 E（平台工具化）。

把 report.py cmd_collect 的「写文件」流程拆成 4 个纯函数：
  fetch_core → probe_bots → scan_pages → analyze

每个函数：输入 dict / 基本类型，输出 dict，不碰文件系统。
进程内用 MemoryCache 去重 HTTP 请求（单函数内部），跨函数不共享缓存
（平台工具是独立进程，缓存不可跨——citability 会复抓首页，可接受）。

render 也提供 dict 入口（build_report_from_dict），见 render/build.py。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from . import bots as bots_mod
from . import citability as cit_mod
from . import fetch as fetch_mod
from . import robots as robots_mod
from . import schema_check as schema_mod
from . import technical as tech_mod
from .cache import MemoryCache
from .config import cfg
from . import http_utils


def _ensure_cache():
    """每个工具函数入口装记忆缓存（进程内去重，进程间不共享）。

    幂等：已装配则不重置，避免并发调用时互相覆盖缓存。
    """
    if http_utils._CACHE is None:
        http_utils.set_cache(MemoryCache())


def _err(msg: str) -> dict:
    return {"error": msg}


def _safe(fn, label: str = None):
    """包装一个函数，把异常转成 _err dict（单步失败不阻断整体）。

    label 用于错误消息前缀；不传则用 fn.__name__。
    返回一个可调用对象，签名与 fn 相同。
    """
    def _wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            prefix = label or getattr(fn, "__name__", "调用")
            return _err(f"{prefix}失败：{e}")
    return _wrapper


def _safe_with_retry(fn, label: str = None, max_retries: int = 1, retry_delay: float = 4.0):
    """_safe 的加强版：检测到响应被截断（truncated_body=True）时串行重试。

    背景（2026-11 修）：Cloudflare 速率限制会截断响应到 <head> 末尾、<body> 完全
    缺失，fetch_page 拿到 truncated_body=True 的结果——meta/JSON-LD 完整但
    h1/internal_links/images 全空、word_count 只有 title 词数（典型 5-10）。
    这种截断是瞬时的，串行重试 1 次（间隔 retry_delay 秒）通常能拿到完整正文。

    只在 truncated_body 时重试：其他错误（404、连接失败、SSL 错误）重试无意义；
    也避免 challenge_detected=True 时重复触发 WAF。
    """
    def _wrapper(*args, **kwargs):
        import time
        last_result = None
        for attempt in range(max_retries + 1):
            result = _safe(fn, label)(*args, **kwargs)
            last_result = result
            if isinstance(result, dict) and result.get("truncated_body"):
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                # 重试用尽仍截断——在 errors 里追加提示
                if isinstance(result.get("errors"), list):
                    result["errors"].append(
                        f"截断重试 {max_retries} 次后仍未拿到完整 <body>，"
                        "建议下次诊断或降低请求频率"
                    )
            break
        return last_result
    return _wrapper


# ── 工具 1：核心抓取 ─────────────────────────────────────────

def fetch_core(url: str) -> dict:
    """首页抓取 + robots + 技术层。

    返回：{fetch, robots, technical}
    单步失败不阻断：对应子 dict 写 {"error": "..."}。

    串行执行：fetch_page → fetch_robots → check_technical。
    原实现是 fetch_page 与 fetch_robots 并发跑（ThreadPoolExecutor max_workers=2），
    同 host 瞬间 2-3 个请求会触发 Cloudflare 速率限制——首页响应被中途截断
    （707KB → 416KB），word_count 从 1238 掉到 171，并被 SSR 判定逻辑误标
    「纯客户端渲染」。report.py 串行执行从未触发，故改回串行（2026-11 修）。
    """
    _ensure_cache()
    out = {"url": url}

    # fetch_page 走截断重试；robots/technical 是 XML/文本响应，不适用截断逻辑
    out["fetch"] = _safe_with_retry(fetch_mod.fetch_page, "fetch", max_retries=1, retry_delay=4.0)(url)
    out["robots"] = _safe(robots_mod.fetch_robots, "robots 解析")(url)

    page = out["fetch"]
    try:
        out["technical"] = tech_mod.check_technical(
            url,
            security_headers=page.get("security_headers", {}) if "error" not in page else {},
        )
    except Exception as e:
        out["technical"] = _err(f"技术层检测失败：{e}")

    return out


# ── 工具 2：爬虫实测 + 可引用性 ─────────────────────────────

def _apply_robots_cap(bots: dict, robots: dict) -> dict:
    """声明层 vs 实测层一致性约束：robots.txt 全禁时，实测可达性不得满分。

    背景：robots.txt 是「合同」，UA 重放是「现状」。OpenAI/Anthropic 的爬虫
    当前可能忽略 robots 继续抓（实测 200），但政策随时收紧——声明全禁
    意味着「配置意图就是不让爬」，与实测满分矛盾。封顶 60 分，报告里
    才不会同时出现「P0 阻塞」与「可达性满分」。
    封顶时在 bots 里写入人类可读原因，供模板与平台 agent 引用。
    """
    if not isinstance(bots, dict) or not isinstance(robots, dict):
        return bots
    if not robots.get("exists"):
        return bots
    status_map = robots.get("ai_crawler_status") or {}
    blocked_names = [n for n, s in status_map.items()
                     if s in ("BLOCKED", "BLOCKED_BY_WILDCARD")]
    if blocked_names:
        score = bots.get("overall_score")
        if isinstance(score, int) and score > 60:
            bots["overall_score"] = 60
            bots["robots_cap_applied"] = True
            bots["robots_cap_reason"] = (
                f"robots.txt 对 {len(blocked_names)} 个爬虫声明全禁"
                f"（{'、'.join(blocked_names[:4])}{' 等' if len(blocked_names) > 4 else ''}）。"
                "UA 重放实测虽全部可达，但配置意图与现状矛盾——爬虫当前忽略 robots 继续抓，"
                "一旦其政策收紧，可达性将即刻归零。实测分由 100 封顶至 60，"
                "修复 robots.txt 后自动恢复。")
    return bots


def probe_bots(url: str, robots: dict = None) -> dict:
    """12 个 AI 爬虫 UA 重放 + 段落可引用性评分。

    返回：{bots, citability}
    注意：citability 会复抓一次首页（跨进程无缓存，1 次请求可接受）。

    串行执行：bots 先、citability 后。原实现两者并发（max_workers=2），
    bots 12 个 UA 重放与 citability 复抓同时打向同 host，会触发 Cloudflare
    速率限制（BingBot/CCBot status=0、首页响应截断）。改为串行（2026-11 修）。
    """
    _ensure_cache()
    bots = _safe(bots_mod.probe_bots, "爬虫实测")(url,
                 delay=float(cfg("crawler.polite_delay", 1)))
    bots = _apply_robots_cap(bots, robots)
    citability = _safe(cit_mod.score_citability, "可引用性评分")(url)
    return {"bots": bots, "citability": citability}


# ── 工具 3：页面扫描（抽样 + 功能页 + schema） ───────────────

def scan_pages(url: str, fetch_page: dict) -> dict:
    """文章/产品页抽样抓取 + 功能页发现 + schema 检测。

    参数 fetch_page 来自工具 1 的 fetch 结果（平台变量传递），
    用它的 internal_links 做采样、structured_data 构建 schema，避免复抓首页。

    返回：{articles, pages, schema}
      - articles 恒带 pending_agent_pick: true（Phase 1.5 每次诊断都触发），
        平台 LLM 节点复核/补充 URL 后调 rescan_pages 补齐
    """
    _ensure_cache()
    from . import discovery, sampling
    from .config import cfg

    out = {}
    delay = float(cfg("crawler.polite_delay", 1))
    internal_links = fetch_page.get("internal_links", []) if "error" not in fetch_page else []

    # 抽样
    try:
        article_urls = sampling.pick_article_urls(
            internal_links, limit=cfg("crawler.sample_articles", 5))
        product_urls = sampling.pick_product_urls(
            internal_links, limit=cfg("crawler.sample_products", 3))

        # 列表页展开：SPA 站点（如 React SPA）首页服务端 HTML 只有导航级
        # 列表页链接（/blog、/insights），详情页由 JS 渲染、不在 internal_links
        # 里。此时 pick_article_urls 必然为空，平台 LLM 拿到空候选只能把列表页
        # 当文章回传（rescan 抓到的是列表页 → 无日期 → 误判「全站无机器可读
        # 日期」）。先把列表页抓一层，把它内部的详情页链接并入候选。
        list_expanded = []
        if not article_urls and cfg("crawler.expand_list_pages", True):
            list_pages = sampling.pick_list_pages(
                internal_links, limit=cfg("crawler.list_page_limit", 3))
            if list_pages:
                list_expanded = _expand_list_pages(list_pages, delay)
                merged = internal_links + list_expanded
                article_urls = sampling.pick_article_urls(
                    merged, limit=cfg("crawler.sample_articles", 5))

        # sitemap 兜底：若 internal_links 与列表页展开都没给出内容详情页
        # （首页导航只露出产品/功能页，内容藏在 /guides 下未链——pockella.com
        # 案例：/guides 仅在 sitemap.xml 出现，46 条 internal_links 无一条指向
        # 它），直接解析 sitemap 作为兜底候选源。优先在「首页内链 + sitemap URL」
        # 上跑 pick_article_urls（内容详情页通常都在 sitemap 里），不渲染抓取
        # 列表页，省一次 HTTP。
        if not article_urls and cfg("crawler.sitemap_fallback", True):
            from . import sitemap as _sitemap_mod
            sm_urls = _sitemap_mod.discover_sitemaps(
                url, limit=int(cfg("crawler.sitemap_limit", 200)))
            if sm_urls:
                sm_links = [{"url": u, "text": ""} for u in sm_urls]
                article_urls = sampling.pick_article_urls(
                    internal_links + list_expanded + sm_links,
                    limit=cfg("crawler.sample_articles", 5))
                if article_urls:
                    list_expanded = list_expanded or []
                    # 把 sitemap 候选也透给 agent：它从 internal_links 挑 URL
                    # 时能看到内容详情页，避免「从 internal_links 挑不到文章」
                    # 时只回传产品页/功能页。
                    list_expanded = list_expanded + sm_links

        articles = {"sampled": article_urls, "sampled_products": product_urls,
                    "pages": [], "product_pages": [],
                    "pending_agent_pick": True, "internal_links": internal_links}
        if list_expanded:
            # 展开结果必须回传给平台 LLM：它只能从 internal_links 里挑 URL，
            # 若不合并，agent 看到的仍是只有列表页的两条导航链接。
            articles["internal_links"] = internal_links + list_expanded
            articles["list_pages_expanded"] = True
        if article_urls or product_urls:
            _fetch_urls_into(articles, article_urls, product_urls, delay)
    except Exception as e:
        articles = _err(f"页面采样失败：{e}")
    out["articles"] = articles

    # 功能页发现（截断重试，2026-11 修）
    try:
        fetch_one_with_retry = lambda u: _safe_with_retry(
            fetch_mod.fetch_page, "fetch", max_retries=1, retry_delay=4.0)(u)
        out["pages"] = discovery.audit_functional_pages(
            url, internal_links, fetch_one=fetch_one_with_retry)
    except Exception as e:
        out["pages"] = _err(f"功能页发现失败：{e}")

    # schema（首页 structured_data 直接来自入参，零复抓）
    try:
        out["schema"] = build_schema_from(fetch_page, articles)
    except Exception as e:
        out["schema"] = _err(f"schema 检测失败：{e}")

    return out


def build_schema_from(fetch_page: dict, articles: dict) -> dict:
    """独立的小函数：从 fetch + articles 构建 schema.json 数据。

    平台编排时放在工具 3 之后调用（或在工具 3 内部通过额外参数完成）。
    保持与 report.py _build_schema 完全一致的逻辑。
    """
    sd = list(fetch_page.get("structured_data", [])) if "error" not in fetch_page else []
    fetch_errors = list(fetch_page.get("errors", [])) if "error" not in fetch_page else []
    for ap in articles.get("pages", []):
        sd.extend(ap.get("structured_data", []) or [])
        fetch_errors.extend(ap.get("errors", []) or [])
    for pp in articles.get("product_pages", []):
        sd.extend(pp.get("structured_data", []) or [])
        fetch_errors.extend(pp.get("errors", []) or [])
    sampled = {
        "homepage": 1 if "error" not in fetch_page else 0,
        "article_pages": sum(1 for ap in articles.get("pages", []) if "error" not in ap),
        "product_pages": sum(1 for pp in articles.get("product_pages", []) if "error" not in pp),
    }
    is_ecom = schema_mod.detect_ecommerce(
        fetch_page.get("internal_links", []) if "error" not in fetch_page else [])
    schema = schema_mod.check_schema(
        sd, sampled=sampled, is_ecommerce=is_ecom, fetch_errors=fetch_errors)
    schema["sampled_pages"] = sum(sampled.values())
    return schema


def rescan_pages(agent_picks: dict, fetch_page: dict) -> dict:
    """pending_agent_pick 分支：用平台 LLM 挑的 URL 补抓。

    agent_picks: {"articles": [...], "products": [...]}
    fetch_page:  工具 1 的 fetch 结果（平台变量回传，避免复抓首页）
    返回：{articles, schema}（schema 用补抓后的数据重建）
    """
    _ensure_cache()
    from .config import cfg
    delay = float(cfg("crawler.polite_delay", 1))

    article_urls = [u for u in agent_picks.get("articles") or [] if isinstance(u, str)]
    product_urls = [u for u in agent_picks.get("products") or [] if isinstance(u, str)]

    # 上限截断（提示词要求 3–5 篇 / 2–3 个）：串行抓取 × polite_delay 会撑爆
    # 工具 120s 超时，超量时静默截断并在响应里回显实际采样数。
    max_articles = int(cfg("crawler.sample_articles", 5))
    max_products = int(cfg("crawler.sample_products", 3))
    dropped = {"articles": max(0, len(article_urls) - max_articles),
               "products": max(0, len(product_urls) - max_products)}
    article_urls = article_urls[:max_articles]
    product_urls = product_urls[:max_products]

    articles = {"sampled": article_urls, "sampled_products": product_urls,
                "pages": [], "product_pages": []}
    _fetch_urls_into(articles, article_urls, product_urls, delay)

    try:
        schema = build_schema_from(fetch_page, articles)
    except Exception as e:
        schema = _err(f"schema 重建失败：{e}")

    return {"articles": articles, "schema": schema,
            # 供上游核对：picks 超过上限时告知实际采样数与丢弃数
            "truncated": {k: v for k, v in dropped.items() if v} or None}


def _expand_list_pages(list_pages: list, delay: float) -> list:
    """抓取栏目列表页，提取其中的详情页链接（供 agent 挑选）。

    SPA 站点首页 internal_links 只有导航链接时，详情页 URL 只存在于列表页的
    服务端 HTML 中。这里抓一层列表页、收集其 internal_links，返回形如
    首页 internal_links 的 [{url, text}] 列表。

    失败不阻断：某列表页抓取失败只跳过它；全部失败返回空列表（调用方按
    「没有可展开的候选」处理，退回原行为）。
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _grab(u: str) -> dict:
        # 列表页抓取也走截断重试（2026-11 修）
        return {"url": u, "page": _safe_with_retry(
            fetch_mod.fetch_page, "fetch", max_retries=1, retry_delay=4.0)(u)}

    # 列表页数量少（默认 ≤3），并发抓取不必加锁限速
    results = []
    with ThreadPoolExecutor(max_workers=min(3, len(list_pages))) as pool:
        futures = [pool.submit(_grab, u) for u in list_pages]
        for fut in as_completed(futures):
            results.append(fut.result())

    merged, seen = [], set()
    for r in results:
        page = r.get("page") or {}
        if not isinstance(page, dict) or page.get("error") or not page:
            continue
        for link in page.get("internal_links") or []:
            url = link.get("url") if isinstance(link, dict) else str(link)
            if not url or url in seen:
                continue
            seen.add(url)
            merged.append(link if isinstance(link, dict) else {"url": url, "text": ""})
    return merged


def _fetch_urls_into(articles: dict, article_urls: list, product_urls: list, delay: float):
    """串行抓取填入 articles dict（与 report.py 同名函数逻辑一致）。

    背景：原实现是 ThreadPoolExecutor(max_workers=4) + 按 host 节流，对
    Cloudflare 类 WAF 来说 4 req/s 同 host 突发会触发速率限制——连接被
    中途切断（status_code=0、ChunkedEncodingError），fetch_page 拿到
    不完整 HTML，word_count 从 1238 掉到 ~170，并被 SSR 判定逻辑误标
    为「纯客户端渲染」。report.py 走纯串行 + time.sleep 就从未触发，
    故 pipeline 改回串行（2026-11 修）。
    """
    import time

    def _fetch_one(u: str) -> dict:
        # 截断重试（2026-11 修）：Cloudflare 速率限制会截断响应到 <head> 末尾，
        # 串行重试 1 次间隔 4s 可修复大部分偶发截断。
        return _safe_with_retry(fetch_mod.fetch_page, "fetch", max_retries=1, retry_delay=4.0)(u)

    for u in article_urls:
        articles["pages"].append(_fetch_one(u))
        time.sleep(delay)
    for u in product_urls:
        articles["product_pages"].append(_fetch_one(u))
        time.sleep(delay)


# ── 工具 4：信号 + 规则引擎 + baseline ──────────────────────

def analyze(fetch_page: dict, articles: dict, pages_audit: dict,
            schema: dict, citability: dict = None, bots: dict = None,
            technical: dict = None, url: str = "", robots: dict = None) -> dict:
    """signals 提取 + analyzers 规则引擎 + 品牌实体 + 确定性 baseline。

    所有输入来自前面工具的平台变量。返回紧凑结果：
      {issues, signals_summary, baseline_scores, brand}
    signals 只保留摘要（裁剪 raw 文本，控制输出大小）。
    """
    _ensure_cache()
    out = {}

    # 品牌实体
    try:
        from . import brand as brand_mod
        out["brand"] = brand_mod.scan_brand(url=url or fetch_page.get("url", ""))
    except Exception as e:
        out["brand"] = _err(f"品牌实体扫描失败：{e}")

    # signals
    try:
        from .signals import gatherer
        found_pages = (pages_audit or {}).get("found", {}) if isinstance(pages_audit, dict) else {}
        signals = gatherer.gather_all(fetch_page, articles, functional_pages=found_pages)
    except Exception as e:
        signals = {"error": f"signals 采集失败：{e}"}

    # analyzers
    try:
        from . import analyzers
        issues = analyzers.run_all(signals)
    except Exception as e:
        issues = {"_error": [{"kind": "pipeline_error", "error": str(e)}]}

    out["issues"] = issues
    out["signals_summary"] = _summarize_signals(signals)

    # P0 阻塞判定（规则同源的唯一来源：报告横幅/执行清单/评分对齐都消费它）
    try:
        from .blockers import compute_p0_blockers
        out["p0_blockers"] = compute_p0_blockers(
            fetch=fetch_page, bots=bots or {}, schema=schema,
            issues=issues, articles=articles, robots=robots)
    except Exception as e:
        out["p0_blockers"] = _err(f"阻塞判定失败：{e}")

    # 确定性 baseline（全量五维）
    try:
        from .scoring.engine import compute_baselines
        out["baseline_scores"] = compute_baselines(
            citability=citability or {}, bots=bots or {},
            technical=technical or {}, schema=schema)
    except Exception as e:
        out["baseline_scores"] = {"error": str(e)}

    return out


def _summarize_signals(signals: dict) -> dict:
    """signals 裁剪：保留键名与 scalar 值，去掉大段 raw 文本。"""
    if not isinstance(signals, dict) or "error" in signals:
        return signals
    summary = {}
    for group, items in signals.items():
        if not isinstance(items, dict):
            summary[group] = items
            continue
        compact = {}
        for k, v in items.items():
            if isinstance(v, dict) and "value" in v:
                val = v["value"]
                # 标量直接留，长文本截断
                compact[k] = val if not isinstance(val, str) or len(val) <= 200 else val[:200] + "…"
            else:
                compact[k] = v
        summary[group] = compact
    return summary


# ── 编排：串行跑全量采集（供 /collect 用） ────────────────────

def fetch_all(url: str, soft_timeout: float = 110.0) -> dict:
    """串行编排 fetch_core / probe_bots / scan_pages，加兜底超时。

    平台网关 timeout 120s，这里 110s 兜底——到点还没跑完的子任务
    返回 {"error": "timeout", "partial": true}，让整体能 HTTP 200 返回，
    避免平台侧拿到 axios timeout 而 LLM 什么都拿不到。

    依赖关系：
      - fetch_core 先跑（其结果 fetch 是 scan_pages 的输入）
      - fetch_core 完成后，probe_bots 与 scan_pages **串行**——原并发实现会
        触发 Cloudflare 速率限制（同 host 4+ req/s 突发），导致首页响应被截断、
        word_count 从 1238 掉到 171，SSR 判定逻辑误标「纯客户端渲染」。
        串行执行单页 1 req/s，Cloudflare 不会介入（2026-11 修）。
    理论耗时（polite_delay=2s）：fetch_core(~10s) + probe_bots(~35s) +
    scan_pages(~30s) ≈ 75s，仍在 110s 内。慢站超 110s 时把 scan_pages 标记
    为 timeout 返回 partial: true，由 agent 后续走 /rescan 补抓 articles/products
    ——这是已有的兜底机制，不丢数据。

    返回结构与原 /collect 完全一致：
      {url, fetch, robots, technical, bots, citability, articles, pages, schema,
       _meta: {elapsed, partial, completed: [...], timed_out: [...]}}
    """
    import time
    t0 = time.time()
    out = {"url": url, "_meta": {"partial": False, "completed": [], "timed_out": []}}

    def _merge(src: dict, mapping: dict):
        """把子任务结果按 mapping 并入 out。mapping: {out_key: src_key}"""
        fallback = src.get("error") if isinstance(src, dict) else None
        for ok, sk in mapping.items():
            out[ok] = src.get(sk, _err(fallback or "子任务未返回"))

    # 阶段 1：fetch_core 必须先跑（scan_pages 依赖它的 fetch）
    core = fetch_core(url)
    _merge(core, {"fetch": "fetch", "robots": "robots", "technical": "technical"})
    out["_meta"]["completed"].append("fetch_core")

    elapsed = time.time() - t0
    remaining = soft_timeout - elapsed
    if remaining <= 0:
        out["_meta"]["partial"] = True
        out["_meta"]["timed_out"] = ["probe_bots", "scan_pages"]
        for k in ("bots", "citability", "articles", "pages", "schema"):
            out[k] = _err("整体超时，未执行")
        out["_meta"]["elapsed"] = round(elapsed, 2)
        return out

    # 阶段 2：probe_bots 与 scan_pages 串行，避免并发突发触发 Cloudflare 限流。
    # 原实现是两者并发（ThreadPoolExecutor max_workers=2），probe_bots 内部
    # 12 个 UA 重放 + scan_pages 内部子页抓取同时打向同 host，导致 BingBot/CCBot
    # 实测 status=0、子页抓失败、首页响应被截断。改为串行（2026-11 修）。
    probe_result = _safe(probe_bots, "爬虫/可引用性")(url, core.get("robots", {}))
    if "error" not in probe_result or "bots" in probe_result:
        _merge(probe_result, {"bots": "bots", "citability": "citability"})
        out["_meta"]["completed"].append("probe_bots")
    else:
        out["_meta"]["partial"] = True
        out["_meta"]["timed_out"].append("probe_bots")
        out["bots"] = probe_result
        out["citability"] = _err("probe_bots 失败，未执行")

    # 若 probe_bots 后剩余时间已不足 scan_pages（polite_delay=2s 时经验值 ~35s），
    # 提前返回 partial，让 agent 走 /rescan 兜底，而不是让请求压 120s 网关超时。
    elapsed = time.time() - t0
    if (soft_timeout - elapsed) < 35.0:
        out["_meta"]["partial"] = True
        out["_meta"]["timed_out"].append("scan_pages")
        out["articles"] = {
            "sampled": [], "sampled_products": [], "pages": [], "product_pages": [],
            "pending_agent_pick": True,
            "internal_links": core.get("fetch", {}).get("internal_links", []),
            "_timeout_fallback": "scan_pages 因 55s 软超时未执行，请调 /rescan 补抓"
        }
        out["pages"] = _err("scan_pages 因 55s 软超时未执行")
        out["schema"] = _err("scan_pages 因 55s 软超时未执行")
        out["_meta"]["elapsed"] = round(time.time() - t0, 2)
        return out

    scan_result = _safe(scan_pages, "页面扫描")(url, core.get("fetch", {}))
    if "error" not in scan_result or "articles" in scan_result:
        _merge(scan_result, {"articles": "articles", "pages": "pages", "schema": "schema"})
        out["_meta"]["completed"].append("scan_pages")
    else:
        out["_meta"]["partial"] = True
        out["_meta"]["timed_out"].append("scan_pages")
        for k in ("articles", "pages", "schema"):
            out[k] = scan_result

    out["_meta"]["elapsed"] = round(time.time() - t0, 2)
    return out
