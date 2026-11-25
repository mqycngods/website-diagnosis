"""FastAPI 服务：把 diagnose/render 包成 agent 平台的工具端点。

端点设计贴合「平台工具化」工作流：
  POST /collect  → 抓取所有确定性事实，结果按 run_id 暂存服务端
  POST /rescan   → 动态站兜底：按 run_id 取已抓数据补抓，重建 articles/schema
  POST /analyze  → 按 run_id 取数据，汇总成 signals/issues/baseline/brand
  POST /render   → 按 run_id 取数据 + LLM 的 scores，渲染报告并落盘 results/
  GET  /results/... → 在线访问报告（HTML 直接渲染，MD 纯文本预览）

两个平台侧现实，服务端全部兜底：
  1. 平台模板引擎靠 {{var}} 原样替换拼请求体——大 JSON 变量为空、或 dict 被
     str() 成单引号形式，body 都不是合法 JSON。所以四个端点都**手动读原始
     body**，用 json → ast.literal_eval → 引号替换 三层兜底解析，
     永远不给平台返回 422 json_invalid。
  2. 大数据按 run_id 暂存服务端（TTL 2h），平台只传 run_id 和小参数。

注意：run_id 暂存后端由 STORE_BACKEND 选择。
  - memory（默认）：进程内存，单副本可用，多副本会跨请求丢 run_id。
  - redis：配 REDIS_URL 后启用，支持多副本与滚动更新。
results/ 落盘在 Render 免费档是临时的，重新部署会丢。

启动：
  pip install -r requirements.txt
  SITE_DIAGNOSIS_API_KEY=<与平台一致的key> uvicorn tools.api_server:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# 让 tools/ 能 import 项目根的 diagnose/render 包
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Security, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel

from diagnose import pipeline
from render.build import build_report_from_dict
from render.html import render_html_string

app = FastAPI(
    title="site-diagnosis-zh API",
    description="中文官网 GEO 诊断工具集 — 供 agent 平台工作流调用",
    version="2.1.0",
)


@app.middleware("http")
async def _log_requests(request: Request, call_next):
    resp = await call_next(request)
    print(f"[api] {request.method} {request.url.path} → {resp.status_code}")
    return resp


# 平台网关常加 /api 前缀，挂一份别名路由让两种路径都通
api_router = APIRouter(prefix="/api")


# ── API Key 鉴权 ─────────────────────────────────────────────
_API_KEY = os.environ.get("SITE_DIAGNOSIS_API_KEY", "dev-key-please-change-me")
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(key: str = Security(_api_key_header)):
    if not key or key != _API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或缺失 X-API-Key 请求头",
        )
    return key


# ── 原始 body 分层兜底解析 ───────────────────────────────────
def _loose_extract(text: str) -> dict:
    """最后一层救援：body 因变量原样替换（Markdown 真实换行/未转义引号）
    变成非法 JSON 时，按已知键名切片提取标量值。

    模板键序固定（run_id, url, scores, plan_md），plan_md 总是最后一个
    键，其值直接截到结尾 '}';其他键值截到下一个已知键出现处。
    scores 等对象值截断后由 _as_json 再尝试解析,失败按缺省处理。
    """
    keys = ("run_id", "url", "scores", "plan_md", "articles", "products",
            "agent_picks", "domain", "offline", "inputs")
    hits = []
    for k in keys:
        m = re.search(rf'"{k}"\s*:\s*', text)
        if m:
            hits.append((m.start(), k, m.end()))
    hits.sort()
    out = {}
    for idx, (_, key, val_start) in enumerate(hits):
        if idx + 1 < len(hits):
            val = text[val_start:hits[idx + 1][0]]
        else:
            val = text[val_start:text.rfind("}") if "}" in text else len(text)]
        val = val.strip().rstrip(",").strip()
        if val.startswith('"'):
            j = val.rfind('"')
            val = val[1:j] if j > 0 else val.strip('"')
            val = val.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        out[key] = val
    return out


def _parse_body(raw: bytes) -> dict:
    """平台模板拼出的 body 可能不是合法 JSON（单引号 dict、变量为空等）。

    分层兜底：json.loads → ast.literal_eval（单引号 Python dict）→
    单引号换双引号再试 → 按键名粗提取（Markdown 未转义场景）→ {}，
    绝不 422 json_invalid。
    """
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        v = ast.literal_eval(text)
        if isinstance(v, dict):
            return v
    except Exception:
        pass
    try:
        return json.loads(text.replace("'", '"'))
    except Exception:
        pass
    return _loose_extract(text)


def _as_json(x: Any) -> Any:
    """变量值可能是 JSON 字符串 / dict / list / None，统一还原成 Python 对象。"""
    if x is None or isinstance(x, (dict, list)):
        return x
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return None
        try:
            return json.loads(s)
        except Exception:
            pass
        try:
            v = ast.literal_eval(s)
            return v if isinstance(v, (dict, list)) else None
        except Exception:
            return None
    return x


# ── run_id 暂存 ──────────────────────────────────────────────
# 后端由环境变量选择：
#   STORE_BACKEND=memory（默认）—— 进程内存 dict，单副本可用
#   STORE_BACKEND=redis        —— 需同时配 REDIS_URL，支持多副本/滚动更新
_STORE_TTL = 2 * 3600   # 2 小时过期
_STORE_MAX = 50         # 仅 memory 后端生效：最多同时保留 50 次诊断
_STORE_BACKEND = os.environ.get("STORE_BACKEND", "memory").strip().lower()
_REDIS_URL = os.environ.get("REDIS_URL", "").strip()
_STORE_KEY_PREFIX = "sdzh:run:"


class _RunStore:
    """run_id → dict 的暂存接口。get 未命中统一抛 404。"""

    def put(self, run_id: str, data: dict) -> None:
        raise NotImplementedError

    def get(self, run_id: str) -> dict:
        raise NotImplementedError

    def delete(self, run_id: str) -> None:
        raise NotImplementedError


class _MemoryStore(_RunStore):
    def __init__(self) -> None:
        self._data: dict = {}

    def put(self, run_id: str, data: dict) -> None:
        now = time.time()
        for k in [k for k, (_, ts) in self._data.items() if now - ts > _STORE_TTL]:
            self._data.pop(k, None)
        while len(self._data) >= _STORE_MAX:
            self._data.pop(min(self._data, key=lambda k: self._data[k][1]), None)
        self._data[run_id] = (data, now)

    def get(self, run_id: str) -> dict:
        item = self._data.get(run_id)
        if item is None or time.time() - item[1] > _STORE_TTL:
            self._data.pop(run_id, None)
            _raise_missing(run_id)
        return item[0]

    def delete(self, run_id: str) -> None:
        self._data.pop(run_id, None)


class _RedisStore(_RunStore):
    """TTL 交给 Redis 的 SETEX；容量上限由 Redis maxmemory-policy 控制。"""

    def __init__(self, url: str) -> None:
        try:
            import redis  # 延迟导入：memory 后端无需安装 redis 包
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "STORE_BACKEND=redis 需要安装 redis 包（pip install redis）"
            ) from e
        # decode_responses=True：读回来直接是 str，省掉一层手工解码
        self._r = redis.Redis.from_url(url, decode_responses=True, socket_timeout=5)

    def _key(self, run_id: str) -> str:
        return f"{_STORE_KEY_PREFIX}{run_id}"

    def put(self, run_id: str, data: dict) -> None:
        try:
            payload = json.dumps(data, ensure_ascii=False, default=str)
            self._r.setex(self._key(run_id), _STORE_TTL, payload)
        except Exception as e:
            # 落库失败比静默成功安全：让上游看到 5xx 而不是后续 run_id 找不到
            raise HTTPException(
                status_code=503,
                detail=f"run_id 暂存失败（Redis 不可用）：{e}",
            ) from e

    def get(self, run_id: str) -> dict:
        try:
            raw = self._r.get(self._key(run_id))
        except Exception as e:
            raise HTTPException(
                status_code=503,
                detail=f"run_id 暂存读取失败（Redis 不可用）：{e}",
            ) from e
        if raw is None:
            _raise_missing(run_id)
        try:
            data = json.loads(raw)
        except Exception as e:
            raise HTTPException(
                status_code=503,
                detail=f"run_id 「{run_id}」暂存数据损坏：{e}",
            ) from e
        if not isinstance(data, dict):
            _raise_missing(run_id)
        return data

    def delete(self, run_id: str) -> None:
        try:
            self._r.delete(self._key(run_id))
        except Exception:
            pass  # 删除失败不影响主流程


def _raise_missing(run_id: str) -> None:
    raise HTTPException(
        status_code=404,
        detail=f"run_id 「{run_id}」不存在或已过期（2 小时有效）——请重新跑 collect-fetch 拿到新的 run_id",
    )


def _make_store() -> _RunStore:
    if _STORE_BACKEND == "redis":
        if not _REDIS_URL:
            raise RuntimeError("STORE_BACKEND=redis 但未配置 REDIS_URL")
        return _RedisStore(_REDIS_URL)
    if _STORE_BACKEND != "memory":
        raise RuntimeError(
            f"未知 STORE_BACKEND={_STORE_BACKEND}（只能是 memory 或 redis）"
        )
    return _MemoryStore()


_STORE: _RunStore = _make_store()


def _store_put(run_id: str, data: dict) -> None:
    _STORE.put(run_id, data)


def _store_get(run_id: str) -> dict:
    return _STORE.get(run_id)


# run_id 无效值黑名单（小写匹配）：LLM 常编这些占位符当 run_id 传过来，
# 不能当成合法 run_id 去 _store_get（会 404）。
_INVALID_RUN_IDS = {
    "", "auto", "none", "null", "undefined",
    "run_id", "<run_id>", "{run_id}",
    "000000000000", "111111111111", "123456789012",
    "xxxxxxxxxxxx", "aaaaaaaaaaaa",
    "<collect_fetch 返回的 run_id 值>",
    "<collect_fetch 返回的 run_id>",
    "{{collect_fetch.run_id}}",
}


def _resolve_run_id(body: dict) -> tuple:
    """从请求 body 解析出有效的 (run_id, stored_dict)。

    严格策略（2026-11 修）：run_id 必须显式传对——不做 url 反查兜底。
    原因：url 反查会让 url 与 run_id 的对应关系错乱（同 url 多次诊断时
    拿错 run_id、串站），比直接 422 报错更危险。LLM 编 run_id 占位符时
    （'auto'/'000000000000'/'<run_id>'），服务端返回明确的 422 + 错误
    提示，让 agent 重试或重新调 collect-fetch，而不是给一个错误的数据。

    返回 (run_id, stored)。stored 保证非空 dict。
    失败抛 422（识别为无效占位符）或 404（run_id 格式合法但 store 未命中，
    可能已过期或服务重启）。
    """
    run_id_raw = str(body.get("run_id") or "").strip()

    # 识别为 LLM 编值：明确 422 报错，不尝试 _store_get（否则返回的 404
    # 会让 agent 误以为 run_id 格式对但过期了，而不是「编了占位符」）。
    if not run_id_raw or run_id_raw.lower() in _INVALID_RUN_IDS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"run_id={run_id_raw!r} 是占位符/编值，不是 collect-fetch 返回的真实 run_id。"
                "请检查：调 collect-fetch 后，从响应 JSON 的 run_id 字段把值原样抄过来"
                "（12 位十六进制字符串），不要自己编。"
            ),
        )

    # run_id 格式合法，查 store——未命中则抛 404（_raise_missing）。
    stored = _store_get(run_id_raw)
    return run_id_raw, stored


# ── 报告落盘 + 在线访问 ──────────────────────────────────────
RESULTS_DIR = _ROOT / "results"
RESULTS_DIR.mkdir(exist_ok=True)
_NAME_SAFE = re.compile(r"[^0-9A-Za-z._-]+")
_FILE_MEDIA = {
    ".html": "text/html; charset=utf-8",
    ".md": "text/plain; charset=utf-8",   # 浏览器内联显示，用作 md 预览
    ".json": "application/json; charset=utf-8",
}


def _base_url(request: Request) -> str:
    """尽力还原对外可访问的 base URL（Render 代理会带 X-Forwarded-* 头）。"""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def _save_report_files(domain: str, report, html_str: str, plan_md: str = "") -> dict:
    """把 HTML / 执行清单 Markdown / 原始数据落盘到 results/<domain>-<ts>/。

    不再落盘渲染器生成的诊断报告 md：md 文件只有执行清单
    <safe>-geo-plan.md（plan_md 为空时不落盘，md_name 为 None，
    md_url 返回 None）。
    """
    safe = _NAME_SAFE.sub("_", domain) or "site"
    dir_name = f"{safe}-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir = RESULTS_DIR / dir_name
    out_dir.mkdir(parents=True, exist_ok=True)

    html_name = f"{safe}-geo-report.html"
    (out_dir / html_name).write_text(html_str, encoding="utf-8")
    md_name = None
    if plan_md.strip():
        md_name = f"{safe}-geo-plan.md"
        (out_dir / md_name).write_text(plan_md, encoding="utf-8")
    (out_dir / "report-data.json").write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, default=str), encoding="utf-8")
    return {"dir_name": dir_name, "html_name": html_name, "md_name": md_name}


# ── 端点 ─────────────────────────────────────────────────────

# 与本地 skill（report.py collect）顺序完全一致的细粒度端点。
# 拆分原因：单 /collect 内部并发会触发 Cloudflare 速率限制（同 host 突发
# 4+ req/s），导致首页响应被截断、word_count 从 1238 掉到 171、SSR 误判
# 「纯客户端渲染」；同时 60s 网关超时压不住一把抓的总耗时。
# 拆成 6 个串行端点后，每个 < 30s，顺序与本地 skill 完全一致：
#   1a. fetch_page              → /collect-fetch（仅首页，~1-5s）
#   1b. robots + technical     → /collect-robots（sitemap 递归可能 10-30s）
#   2. 文章/产品采样抓取        → /collect-sample
#   3. 功能页发现 + schema      → /collect-discover
#   4. bots UA 重放             → /collect-bots
#   5. citability              → /collect-citability
# brand/signals/issues/baseline 由 /analyze 汇总（零网络）。


@app.post("/collect-fetch")
@api_router.post("/collect-fetch")
async def collect_fetch(request: Request, _key: str = Depends(require_api_key)):
    """阶段 1a：仅抓首页（fetch_page，含 title/meta/正文/JSON-LD/links）。

    返回 {url, run_id, fetch}。robots/technical 拆到 /collect-robots——
    sitemap 递归解析与 llms.txt 探测可能耗时 10-30s，单端点会压 60s 网关
    超时（2026-11 修）。
    """
    body = _parse_body(await request.body())
    url = (body.get("url") or (body.get("inputs") or {}).get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=422, detail="缺少 url 参数")

    def _run():
        pipeline._ensure_cache()
        # 截断重试（2026-11 修）：Cloudflare 速率限制会截断响应到 <head> 末尾，
        # <body> 完全缺失（word_count=7，h1/links/images 全空）。串行重试 1 次
        # 间隔 4s，约 80% 概率拿到完整正文。
        return {"url": url, "fetch": pipeline._safe_with_retry(
            pipeline.fetch_mod.fetch_page, "fetch", max_retries=1, retry_delay=4.0)(url)}

    result = await run_in_threadpool(_run)
    # run_id 生成：uuid4 随机 12 位十六进制。允许调用方显式传入 run_id
    # （用于调试/重放），否则随机生成。同 url 多次诊断会产生不同 run_id——
    # 这是正确行为：每次诊断的数据应该独立，不能用 url 派生做兜底（会让
    # 多次诊断串数据，2026-11 修）。
    explicit_run_id = str(body.get("run_id") or "").strip()
    run_id = explicit_run_id if explicit_run_id else uuid.uuid4().hex[:12]
    _store_put(run_id, {"url": url, **result})
    result["run_id"] = run_id
    return result


@app.post("/collect-robots")
@api_router.post("/collect-robots")
async def collect_robots(request: Request, _key: str = Depends(require_api_key)):
    """阶段 1b：robots.txt + sitemap 分析 + technical（安全头/HTTPS/llms.txt）。

    sitemap 递归解析 + llms.txt 探测可能耗时 5-30s（视站点 sitemap 大小）。
    返回 {robots, technical}，写回暂存。
    """
    body = _parse_body(await request.body())
    run_id, stored = _resolve_run_id(body)
    url = stored.get("url") or body.get("url") or ""
    fetch_page = stored.get("fetch") or {}

    def _run():
        pipeline._ensure_cache()
        out = {}
        out["robots"] = pipeline._safe(pipeline.robots_mod.fetch_robots, "robots 解析")(url)
        try:
            out["technical"] = pipeline.tech_mod.check_technical(
                url,
                security_headers=fetch_page.get("security_headers", {}) if "error" not in fetch_page else {},
            )
        except Exception as e:
            out["technical"] = pipeline._err(f"技术层检测失败：{e}")
        return out

    result = await run_in_threadpool(_run)
    stored["robots"] = result["robots"]
    stored["technical"] = result["technical"]
    _store_put(run_id, stored)
    return {"robots": result["robots"], "technical": result["technical"], "run_id": run_id}


@app.post("/collect-sample")
@api_router.post("/collect-sample")
async def collect_sample(request: Request, _key: str = Depends(require_api_key)):
    """阶段 2：文章/产品采样抓取（对应 report.py 步骤 2）。

    从 run_id 暂存取首页 internal_links，串行抓 5 文章 + 3 产品。
    返回 {articles}，写回暂存。articles 恒带 pending_agent_pick=True。
    """
    body = _parse_body(await request.body())
    run_id, stored = _resolve_run_id(body)
    fetch_page = stored.get("fetch") or {}
    url = stored.get("url") or body.get("url") or ""

    def _run():
        pipeline._ensure_cache()
        from diagnose import sampling
        from diagnose.config import cfg
        delay = float(cfg("crawler.polite_delay", 1))
        links = fetch_page.get("internal_links", []) if "error" not in fetch_page else []
        article_urls = sampling.pick_article_urls(links, limit=cfg("crawler.sample_articles", 5))
        product_urls = sampling.pick_product_urls(links, limit=cfg("crawler.sample_products", 3))
        articles = {"sampled": article_urls, "sampled_products": product_urls,
                    "pages": [], "product_pages": [],
                    "pending_agent_pick": True, "internal_links": links}
        if article_urls or product_urls:
            pipeline._fetch_urls_into(articles, article_urls, product_urls, delay)
        return articles

    articles = await run_in_threadpool(_run)
    stored["articles"] = articles
    _store_put(run_id, stored)
    return {"articles": articles, "run_id": run_id}


@app.post("/collect-discover")
@api_router.post("/collect-discover")
async def collect_discover(request: Request, _key: str = Depends(require_api_key)):
    """阶段 3：功能页发现 + schema 检测（对应 report.py 步骤 3 + 4）。

    返回 {pages, schema}，写回暂存。
    """
    body = _parse_body(await request.body())
    run_id, stored = _resolve_run_id(body)
    fetch_page = stored.get("fetch") or {}
    articles = stored.get("articles") or {}
    url = stored.get("url") or body.get("url") or ""

    def _run():
        pipeline._ensure_cache()
        from diagnose import discovery
        # 功能页抓取也走截断重试（2026-11 修）
        fetch_one_with_retry = lambda u: pipeline._safe_with_retry(
            pipeline.fetch_mod.fetch_page, "fetch", max_retries=1, retry_delay=4.0)(u)
        pages_audit = discovery.audit_functional_pages(
            url,
            fetch_page.get("internal_links", []) if "error" not in fetch_page else [],
            fetch_one=fetch_one_with_retry)
        schema = pipeline.build_schema_from(fetch_page, articles)
        return {"pages": pages_audit, "schema": schema}

    result = await run_in_threadpool(_run)
    stored["pages"] = result["pages"]
    stored["schema"] = result["schema"]
    _store_put(run_id, stored)
    return {"pages": result["pages"], "schema": result["schema"], "run_id": run_id}


@app.post("/collect-bots")
@api_router.post("/collect-bots")
async def collect_bots(request: Request, _key: str = Depends(require_api_key)):
    """阶段 4：12 个 AI 爬虫 UA 重放（对应 report.py 步骤 6）。

    内部串行 + delay=1s，12 个 UA 约 12-15s。
    返回 {bots}，写回暂存。
    """
    body = _parse_body(await request.body())
    run_id, stored = _resolve_run_id(body)
    url = stored.get("url") or body.get("url") or ""
    robots = stored.get("robots") or {}

    def _run():
        pipeline._ensure_cache()
        from diagnose.config import cfg
        bots = pipeline.bots_mod.probe_bots(url, delay=float(cfg("crawler.polite_delay", 1)))
        return pipeline._apply_robots_cap(bots, robots)

    bots = await run_in_threadpool(_run)
    stored["bots"] = bots
    _store_put(run_id, stored)
    return {"bots": bots, "run_id": run_id}


@app.post("/collect-citability")
@api_router.post("/collect-citability")
async def collect_citability(request: Request, _key: str = Depends(require_api_key)):
    """阶段 5：段落可引用性评分（对应 report.py 步骤 7）。

    会复抓一次首页（跨进程无缓存）。
    返回 {citability}，写回暂存。
    """
    body = _parse_body(await request.body())
    run_id, stored = _resolve_run_id(body)
    url = stored.get("url") or body.get("url") or ""

    citability = await run_in_threadpool(pipeline.cit_mod.score_citability, url)
    stored["citability"] = citability
    _store_put(run_id, stored)
    return {"citability": citability, "run_id": run_id}


@app.post("/collect")
@api_router.post("/collect")
async def collect(request: Request, _key: str = Depends(require_api_key)):
    """一站式抓取（兼容旧流程）：fetch_core + probe_bots + scan_pages 串行。

    新接入请改用 /collect-fetch + /collect-sample + /collect-discover +
    /collect-bots + /collect-citability 五个细粒度端点，与本地 skill
    顺序完全一致，避免 60s 网关超时与 Cloudflare 限流。

    返回 {url, run_id, fetch, robots, technical, bots, citability,
          articles, pages, schema, _meta}。
    后续 rescan/analyze/render 只需回传 run_id，不必搬运大 JSON。
    """
    body = _parse_body(await request.body())
    url = (body.get("url") or (body.get("inputs") or {}).get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=422, detail="缺少 url 参数")

    result = await run_in_threadpool(pipeline.fetch_all, url, 110.0)
    run_id = str(body.get("run_id") or "").strip() or uuid.uuid4().hex[:12]
    _store_put(run_id, {"url": url, **result})
    result["run_id"] = run_id
    return result


@app.post("/rescan")
@api_router.post("/rescan")
async def rescan(request: Request, _key: str = Depends(require_api_key)):
    """动态站补抓：LLM 挑完 URL 后回传，重建 articles + schema。

    传 run_id 时 fetch_page 自动从暂存取；返回 {articles, schema, run_id}，
    并已同步写回暂存（后续 analyze/render 直接用）。
    """
    body = _parse_body(await request.body())
    run_id_raw = str(body.get("run_id") or "").strip()
    url_raw = (body.get("url") or (body.get("inputs") or {}).get("url") or "").strip()
    stored = None
    run_id = ""
    if run_id_raw or url_raw:
        try:
            run_id, stored = _resolve_run_id(body)
        except HTTPException:
            run_id, stored = "", None
    if stored is not None:
        fetch_page = stored.get("fetch") or {}
    else:
        fetch_page = _as_json(body.get("fetch_page")) or {}
        if not fetch_page:
            raise HTTPException(
                status_code=422,
                detail="缺少有效 run_id（可能已过期或服务重启），且未提供 fetch_page 采集数据；"
                       "rescan 需要 collect-fetch 阶段的 run_id，或显式回传 fetch_page。")

    # agent_picks 接受两种形态（2026-11 统一）：
    #   · 平铺 {articles:[...], products:[...]}     ← 工具声明的形态，推荐
    #   · 嵌套 {agent_picks:{articles:[...], ...}}  ← 旧提示词形态，兼容保留
    # 旧实现只读嵌套，而工具声明是平铺，agent 按声明传时 picks 恒为空，
    # rescan 静默补抓 0 个 URL、上游无从察觉。
    picks = _as_json(body.get("agent_picks"))
    if not isinstance(picks, dict):
        flat = {k: _as_json(body.get(k)) for k in ("articles", "products")}
        picks = {k: v for k, v in flat.items() if v}
    if not isinstance(picks, dict):
        picks = {}

    result = await run_in_threadpool(pipeline.rescan_pages, picks, fetch_page)

    if stored is not None:
        # 增量合并而非全量覆盖：diagnose_run 阶段 collect 已自动抽样并抓取过
        # 一批文章/产品页，agent 的 site_rescan 是在此基础上补挑。若直接覆盖，
        # 服务端的采样结果会被丢弃——当站点首页不链向内容详情页（SPA 导航或
        # 内容藏在 /guides 下而首页未链），agent 从 internal_links 挑不到文章
        # 时 rescan 会返回空 articles，把 collect 阶段采到的样本清掉，后续
        # freshness/blocker 计算在残缺样本上误判（pockella.com 案例：
        # collect 抓到 /guides 带 2026-11-12 dateModified，agent 未补挑该 URL
        # 却被 rescan 清空，导致「全站无机器可读日期」误报）。
        prev = stored.get("articles") or {}
        new_articles = result.get("articles") or {}
        stored["articles"] = {
            "sampled":          _merge_unique(prev.get("sampled", []), new_articles.get("sampled", [])),
            "sampled_products": _merge_unique(prev.get("sampled_products", []), new_articles.get("sampled_products", [])),
            "pages":            _merge_pages(prev.get("pages", []), new_articles.get("pages", [])),
            "product_pages":    _merge_pages(prev.get("product_pages", []), new_articles.get("product_pages", [])),
            "internal_links":   prev.get("internal_links", []),
            "pending_agent_pick": False,
        }
        if prev.get("list_pages_expanded"):
            stored["articles"]["list_pages_expanded"] = True
        stored["schema"] = result.get("schema") or stored.get("schema") or {}
        # 补抓会改变 articles/schema，基于旧数据的 P0 判定随之失效——
        # 必须清掉，让 render 用新数据重算（否则报告展示陈旧阻塞清单）。
        stored.pop("p0_blockers", None)
        _store_put(run_id, stored)
        result["run_id"] = run_id
        result["articles"] = stored["articles"]
    return result


def _merge_unique(a: list, b: list) -> list:
    """按 URL 去重合并两个列表（元素可以是字符串或含 url 字段的 dict）。"""
    seen, out = set(), []
    for x in list(a or []) + list(b or []):
        key = x.get("url") if isinstance(x, dict) else x
        if key in seen:
            continue
        seen.add(key)
        out.append(x)
    return out


def _merge_pages(a: list, b: list) -> list:
    """按 url 合并两个页面抓取结果列表，后到的非 error 结果覆盖先前的 error。"""
    by_url: dict = {}
    for p in list(a or []) + list(b or []):
        if not isinstance(p, dict):
            continue
        u = p.get("url")
        if not u:
            continue
        if u not in by_url or ("error" in by_url[u] and "error" not in p):
            by_url[u] = p
    return list(by_url.values())


@app.post("/analyze")
@api_router.post("/analyze")
async def analyze(request: Request, _key: str = Depends(require_api_key)):
    """纯计算：signals + analyzers + baseline_scores + brand。

    传 run_id 时所有大字段从暂存取；返回 {issues, signals_summary,
    baseline_scores, brand, p0_blockers}，并已写回暂存供 /render 使用。
    p0_blockers 是 P0 阻塞的确定性判定（diagnose/blockers.py），报告横幅与
    执行清单前置判定都以它为准（规则同源）。
    """
    body = _parse_body(await request.body())
    run_id_raw = str(body.get("run_id") or "").strip()
    url_raw = (body.get("url") or (body.get("inputs") or {}).get("url") or "").strip()
    if run_id_raw or url_raw:
        try:
            run_id, stored = _resolve_run_id(body)
        except HTTPException:
            run_id, stored = "", {}
        if stored:
            fetch_page = stored.get("fetch") or {}
            articles = stored.get("articles") or {}
            pages_audit = stored.get("pages") or {}
            schema = stored.get("schema") or {}
            citability = stored.get("citability")
            bots = stored.get("bots")
            technical = stored.get("technical")
            robots = stored.get("robots")
            url = url_raw or stored.get("url", "")
        else:
            fetch_page = _as_json(body.get("fetch_page")) or {}
            articles = _as_json(body.get("articles")) or {}
            pages_audit = _as_json(body.get("pages_audit")) or {}
            schema = _as_json(body.get("schema")) or {}
            citability = _as_json(body.get("citability"))
            bots = _as_json(body.get("bots"))
            technical = _as_json(body.get("technical"))
            robots = _as_json(body.get("robots"))
            url = url_raw
            if not any((fetch_page, articles, schema, citability, bots, technical)):
                raise HTTPException(
                    status_code=422,
                    detail="缺少有效 run_id 且未提供采集数据；请重新执行 collect-fetch 获取新的 run_id。")
    else:
        fetch_page = _as_json(body.get("fetch_page")) or {}
        articles = _as_json(body.get("articles")) or {}
        pages_audit = _as_json(body.get("pages_audit")) or {}
        schema = _as_json(body.get("schema")) or {}
        citability = _as_json(body.get("citability"))
        bots = _as_json(body.get("bots"))
        technical = _as_json(body.get("technical"))
        robots = _as_json(body.get("robots"))
        url = body.get("url", "")
        # 无 run_id 且没有任何采集数据 → 显式拒绝。否则会在全空输入上跑出
        # 「0 分 + 全部未测量」的伪结果，上游以为分析成功（2026-11 修）。
        if not any((fetch_page, articles, schema, citability, bots, technical)):
            raise HTTPException(
                status_code=422,
                detail="缺少有效 run_id（可能已过期或服务重启），且未提供采集数据；"
                       "请重新执行 collect 步骤获取新的 run_id。")

    result = await run_in_threadpool(
        pipeline.analyze,
        fetch_page=fetch_page,
        articles=articles,
        pages_audit=pages_audit,
        schema=schema,
        citability=citability,
        bots=bots,
        technical=technical,
        url=url,
        robots=robots,
    )

    if run_id:
        stored.update({
            "brand": result.get("brand"),
            "issues": result.get("issues"),
            "signals_summary": result.get("signals_summary"),
            "baseline_scores": result.get("baseline_scores"),
            "p0_blockers": result.get("p0_blockers"),
        })
        _store_put(run_id, stored)
    return result


@app.post("/render")
@api_router.post("/render")
async def render(request: Request, _key: str = Depends(require_api_key)):
    """渲染中文 HTML 报告，落盘 results/，并返回在线访问链接。

    传 run_id 时所有大字段从暂存取，body 只需 {run_id, scores, plan_md}。
    scores 可缺省/格式不合法——缺省时渲染器用确定性数据生成兜底发现。
    plan_md 为 GEO 执行清单 Markdown：落盘为 <domain>-geo-plan.md，
    md_url 即该清单的在线链接；缺省时 md_url 为 None（不再落盘诊断报告 md）。


    返回 {success, html, size, domain, url, filename, result_dir,
          report_path, report_url, md_path, md_url, is_fallback, error}。
    """
    body = _parse_body(await request.body())
    run_id = str(body.get("run_id") or "").strip()
    raw_scores = body.get("scores")
    scores = _as_json(raw_scores) or {}
    # scores 解析失败必须回传：旧实现静默成 {} → 报告走确定性兜底、
    # 评分与 agent 写的 findings 全部消失，而 success 仍是 true，
    # 上游以为交付成功（2026-11 修）。
    scores_parse_error = None
    if str(raw_scores or "").strip() and not isinstance(scores, dict):
        scores, scores_parse_error = {}, "scores 不是 JSON 对象"
    elif str(raw_scores or "").strip() and not scores:
        scores_parse_error = "scores 无法解析为 JSON（可能含尾逗号/未转义引号）"
    plan_md = str(body.get("plan_md") or "")
    offline = bool(body.get("offline", False))

    if run_id or body.get("url"):
        run_id, stored = _resolve_run_id(body)
        url = body.get("url") or stored.get("url", "")
        data = {
            "fetch": stored.get("fetch") or {},
            "robots": stored.get("robots") or {},
            "bots": stored.get("bots") or {},
            "citability": stored.get("citability") or {},
            "schema": stored.get("schema") or {},
            "brand": stored.get("brand") or {},
            "technical": stored.get("technical") or {},
            "scores": scores,
            "articles": stored.get("articles") or {},
            "signals": stored.get("signals_summary") or {},
            "issues": stored.get("issues") or {},
            "p0_blockers": stored.get("p0_blockers"),
        }
    else:
        url = body.get("url", "")
        data = {
            "fetch": _as_json(body.get("fetch")) or {},
            "robots": _as_json(body.get("robots")) or {},
            "bots": _as_json(body.get("bots")) or {},
            "citability": _as_json(body.get("citability")) or {},
            "schema": _as_json(body.get("schema")) or {},
            "brand": _as_json(body.get("brand")) or {},
            "technical": _as_json(body.get("technical")) or {},
            "scores": scores,
            "articles": _as_json(body.get("articles")) or {},
            "signals": _as_json(body.get("signals_summary")) or {},
            "issues": _as_json(body.get("issues")) or {},
            "p0_blockers": _as_json(body.get("p0_blockers")),
        }
        # 无 run_id 又没有任何采集数据：只能渲染出「全部未测量」的空报告，
        # 还带 success:true——上游会当成成功交付（2026-11 修）。
        if not any(v for k, v in data.items()
                   if k not in ("scores",)):
            raise HTTPException(
                status_code=422,
                detail="缺少有效 run_id（可能已过期或服务重启），且未提供任何采集数据；"
                       "请重新执行 collect → analyze 后再 render。")

    domain = str(body.get("domain") or "") or \
        urlparse(url).netloc.replace("www.", "").replace(":", "_") or "site"
    if not url.strip():
        # 平台 {{var}} 全空时之前会渲染出 site-geo-report.html 垃圾报告，
        # agent 还会把假链接写进最终输出。直接报错让上游发现参数映射问题。
        # detail 里带上定位所需的最小事实，避免只能看到一句「缺少 url」。
        _stored_has_url = "-"
        if run_id:
            try:
                _s = _store_get(run_id)
                _stored_has_url = repr(_s.get("url"))[:120]
            except HTTPException:
                _stored_has_url = "run_id 未命中"
            except Exception as e:  # noqa: BLE001
                _stored_has_url = f"读取异常: {e}"
        raise HTTPException(
            status_code=400,
            detail=(
                "render 缺少 url（run_id 未命中或变量为空）——请检查平台工具的参数映射与请求体"
                f"｜run_id={run_id!r} body_keys={sorted(body.keys())}"
                f" body.url={body.get('url')!r} stored.url={_stored_has_url}"
            ),
        )
    safe_domain = _NAME_SAFE.sub("_", domain) or "site"
    filename = f"{safe_domain}_geo_report.html"

    try:
        report = await run_in_threadpool(build_report_from_dict, data, domain, url)
        html_str = await run_in_threadpool(render_html_string, report, offline)
        saved = _save_report_files(domain, report, html_str, plan_md)
        base = _base_url(request)
        prefix = f"/results/{saved['dir_name']}"
        return {
            "success": True,
            "html": html_str,
            "size": len(html_str),
            "domain": domain,
            "url": url,
            "filename": saved["html_name"],
            "result_dir": f"results/{saved['dir_name']}",
            "report_path": f"{prefix}/{saved['html_name']}",
            "report_url": f"{base}{prefix}/{saved['html_name']}",
            "md_path": f"{prefix}/{saved['md_name']}" if saved["md_name"] else None,
            "md_url": f"{base}{prefix}/{saved['md_name']}" if saved["md_name"] else None,
            "is_fallback": False,
            "error": None,
            "scores_parse_error": scores_parse_error,
        }
    except Exception as e:
        import traceback
        err_detail = traceback.format_exc()
        print(f"[render] 渲染失败，返回兜底报告: {e}\n{err_detail}")
        fallback_html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{domain} - GEO 诊断报告（降级版）</title>
  <style>
    body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; line-height: 1.6; }}
    h1 {{ color: #d32f2f; }}
    .error-box {{ background: #ffebee; border-left: 4px solid #d32f2f; padding: 16px; margin: 20px 0; }}
    .info {{ background: #e3f2fd; border-left: 4px solid #1976d2; padding: 16px; margin: 20px 0; }}
    pre {{ background: #f5f5f5; padding: 12px; overflow-x: auto; font-size: 12px; }}
  </style>
</head>
<body>
  <h1>⚠️ 报告渲染失败（降级版）</h1>
  <div class="error-box">
    <h2>错误信息</h2>
    <p><strong>站点：</strong>{url}</p>
    <p><strong>错误：</strong>{e}</p>
  </div>
  <div class="info">
    <h2>可能原因</h2>
    <ul>
      <li>diagnose_run 抓取失败（站点 WAF/超时/证书问题）</li>
      <li>输入数据结构不符合 render 层预期</li>
      <li>scores 对象格式错误</li>
    </ul>
  </div>
  <details>
    <summary>技术细节（开发者查看）</summary>
    <pre>{err_detail}</pre>
  </details>
</body>
</html>"""
        return {
            "success": False,
            "html": fallback_html,
            "size": len(fallback_html),
            "domain": domain,
            "url": url,
            "filename": filename,
            "is_fallback": True,
            "error": str(e),
        }


@app.get("/results/{dir_name}/{file_name}")
@api_router.get("/results/{dir_name}/{file_name}")
def get_result_file(dir_name: str, file_name: str):
    """在线访问 results/ 下的报告：HTML 直接渲染，MD 以纯文本预览（公开访问）。"""
    if not re.fullmatch(r"[0-9A-Za-z._-]+", dir_name) or not re.fullmatch(r"[0-9A-Za-z._-]+", file_name):
        raise HTTPException(status_code=404, detail="文件不存在")
    path = (RESULTS_DIR / dir_name / file_name).resolve()
    if not str(path).startswith(str(RESULTS_DIR.resolve())) or not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    media = _FILE_MEDIA.get(path.suffix, "application/octet-stream")
    if path.suffix == ".json":
        return Response(content=path.read_text(encoding="utf-8"), media_type=media)
    return Response(content=path.read_bytes(), media_type=media)


@app.get("/health")
@api_router.get("/health")
def health() -> dict:
    return {"status": "ok"}


# 所有 api_router 装饰器之后注册，让 /api/* 与 /* 双路径生效
app.include_router(api_router)
