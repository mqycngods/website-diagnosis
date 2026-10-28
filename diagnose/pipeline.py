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


# ── 工具 1：核心抓取 ─────────────────────────────────────────

def fetch_core(url: str) -> dict:
    """首页抓取 + robots + 技术层。

    返回：{fetch, robots, technical}
    单步失败不阻断：对应子 dict 写 {"error": "..."}。

    内部并发：fetch_page 与 fetch_robots 并发跑（互不依赖）；
    check_technical 依赖 fetch_page 的 security_headers，最后跑。
    串行 ~45s → 并发 ~30s。
    """
    _ensure_cache()
    out = {"url": url}

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_fetch = pool.submit(_safe(fetch_mod.fetch_page, "fetch "), url)
        f_robots = pool.submit(_safe(robots_mod.fetch_robots, "robots 解析"), url)
        out["fetch"] = f_fetch.result()
        out["robots"] = f_robots.result()

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

def probe_bots(url: str) -> dict:
    """12 个 AI 爬虫 UA 重放 + 段落可引用性评分。

    返回：{bots, citability}
    注意：citability 会复抓一次首页（跨进程无缓存，1 次请求可接受）。

    内部并发：bots 与 citability 互不依赖，并发跑。
    串行 ~45s → 并发 ~30s（受限于 bots 的 12 个 UA 串行探测）。
    """
    _ensure_cache()
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_bots = pool.submit(_safe(bots_mod.probe_bots, "爬虫实测"), url)
        f_cit = pool.submit(_safe(cit_mod.score_citability, "可引用性评分"), url)
        return {"bots": f_bots.result(), "citability": f_cit.result()}


# ── 工具 3：页面扫描（抽样 + 功能页 + schema） ───────────────

def scan_pages(url: str, fetch_page: dict) -> dict:
    """文章/产品页抽样抓取 + 功能页发现 + schema 检测。

    参数 fetch_page 来自工具 1 的 fetch 结果（平台变量传递），
    用它的 internal_links 做采样、structured_data 构建 schema，避免复抓首页。

    返回：{articles, pages, schema}
      - articles 可能带 pending_agent_pick: true（正则采样失败时，
        schema 退化为首页面采样；平台 LLM 节点挑 URL 后调 rescan_pages 补齐）
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
        articles = {"sampled": article_urls, "sampled_products": product_urls,
                    "pages": [], "product_pages": []}
        if not article_urls and not product_urls:
            # 正则失效（动态 URL 站），标记让平台 LLM 挑页
            articles["pending_agent_pick"] = True
            articles["internal_links"] = internal_links
        else:
            _fetch_urls_into(articles, article_urls, product_urls, delay)
    except Exception as e:
        articles = _err(f"页面采样失败：{e}")
    out["articles"] = articles

    # 功能页发现
    try:
        out["pages"] = discovery.audit_functional_pages(
            url, internal_links, fetch_one=fetch_mod.fetch_page)
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

    articles = {"sampled": article_urls, "sampled_products": product_urls,
                "pages": [], "product_pages": []}
    _fetch_urls_into(articles, article_urls, product_urls, delay)

    try:
        schema = build_schema_from(fetch_page, articles)
    except Exception as e:
        schema = _err(f"schema 重建失败：{e}")

    return {"articles": articles, "schema": schema}


def _fetch_urls_into(articles: dict, article_urls: list, product_urls: list, delay: float):
    """并发抓取填入 articles dict（与 report.py 同名函数逻辑一致）。

    同 host 最多 4 并发，请求间隔 >= delay 秒（礼貌限速）；不同 host 不互斥。
    串行版本 5 文章 × (30s+1s) ≈ 3min，并发后 ~30s。
    """
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from urllib.parse import urlparse

    # 按 host 分组的锁 + 上次请求时间，保证同 host 间隔 >= delay
    _host_locks: dict = {}
    _host_last: dict = {}
    _locks_guard = threading.Lock()

    def _throttle(host: str):
        with _locks_guard:
            lock = _host_locks.setdefault(host, threading.Lock())
        with lock:
            last = _host_last.get(host, 0)
            wait = delay - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
            _host_last[host] = time.time()

    def _fetch_one(u: str) -> dict:
        _throttle(urlparse(u).netloc)
        try:
            return fetch_mod.fetch_page(u)
        except Exception as e:
            err = _err(f"抓取失败：{e}")
            err["url"] = u
            return err

    # 文章 / 产品分开抓，保持原函数的填充顺序（先文章后产品）
    def _run_batch(urls: list) -> list:
        if not urls:
            return []
        out = [None] * len(urls)
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_fetch_one, u): i for i, u in enumerate(urls)}
            for fut in as_completed(futures):
                out[futures[fut]] = fut.result()
        return out

    articles["pages"].extend(_run_batch(article_urls))
    articles["product_pages"].extend(_run_batch(product_urls))


# ── 工具 4：信号 + 规则引擎 + baseline ──────────────────────

def analyze(fetch_page: dict, articles: dict, pages_audit: dict,
            schema: dict, citability: dict = None, bots: dict = None,
            technical: dict = None, url: str = "") -> dict:
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


# ── 编排：并发跑全量采集（供 /collect 用） ────────────────────

def fetch_all(url: str, soft_timeout: float = 25.0) -> dict:
    """并发编排 fetch_core / probe_bots / scan_pages，加兜底超时。

    平台网关 timeout 通常 30s，这里 25s 兜底——到点还没跑完的子任务
    返回 {"error": "timeout", "partial": true}，让整体能 HTTP 200 返回，
    避免平台侧拿到 axios timeout 而 LLM 什么都拿不到。

    依赖关系：
      - fetch_core 先跑（其结果 fetch 是 scan_pages 的输入）
      - fetch_core 完成后，probe_bots 与 scan_pages 并发
    理论耗时：fetch_core(~25s) + max(probe_bots, scan_pages)(~25s) ≈ 50s
    超 25s 时截断返回部分结果。

    返回结构与原 /collect 完全一致：
      {url, fetch, robots, technical, bots, citability, articles, pages, schema,
       _meta: {elapsed, partial, completed: [...], timed_out: [...]}}
    """
    import time
    t0 = time.time()
    out = {"url": url, "_meta": {"partial": False, "completed": [], "timed_out": []}}

    def _merge(src: dict, mapping: dict):
        """把子任务结果按 mapping 并入 out。mapping: {out_key: src_key}"""
        for ok, sk in mapping.items():
            out[ok] = src.get(sk, _err("子任务未返回"))

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

    # 阶段 2：probe_bots 与 scan_pages 并发，等待剩余时间
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_probe = pool.submit(_safe(probe_bots, "爬虫/可引用性"), url)
        f_scan = pool.submit(_safe(scan_pages, "页面扫描"), url, core.get("fetch", {}))

        done, not_done = {}, {}
        for name, fut in (("probe_bots", f_probe), ("scan_pages", f_scan)):
            try:
                done[name] = fut.result(timeout=remaining)
            except Exception as e:
                # concurrent.futures.TimeoutError 或子任务异常都走这
                not_done[name] = e

        if "probe_bots" in done:
            _merge(done["probe_bots"], {"bots": "bots", "citability": "citability"})
            out["_meta"]["completed"].append("probe_bots")
        else:
            out["_meta"]["partial"] = True
            out["_meta"]["timed_out"].append("probe_bots")
            out["bots"] = _err(f"超时或失败：{not_done['probe_bots']}")
            out["citability"] = _err("超时，未执行")

        if "scan_pages" in done:
            _merge(done["scan_pages"],
                   {"articles": "articles", "pages": "pages", "schema": "schema"})
            out["_meta"]["completed"].append("scan_pages")
        else:
            out["_meta"]["partial"] = True
            out["_meta"]["timed_out"].append("scan_pages")
            for k in ("articles", "pages", "schema"):
                out[k] = _err(f"超时或失败：{not_done['scan_pages']}")

    out["_meta"]["elapsed"] = round(time.time() - t0, 2)
    return out
