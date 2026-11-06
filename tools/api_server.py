"""FastAPI 服务：把 diagnose/render 包成 agent 平台的工具端点。

端点设计贴合「平台工具化」工作流：
  POST /collect  → 抓取所有确定性事实，结果按 run_id 暂存服务端
  POST /rescan   → 动态站兜底：按 run_id 取已抓数据补抓，重建 articles/schema
  POST /analyze  → 按 run_id 取数据，汇总成 signals/issues/baseline/brand
  POST /render   → 按 run_id 取数据 + LLM 的 scores，渲染中文 HTML 报告

为什么用 run_id 暂存：平台模板引擎靠 {{var}} 字符串拼接请求体，
几十 KB 的 JSON 只要有一个变量为空，body 就会被拼成非法 JSON
（422 json_invalid）。把大数据留在服务端（TTL 2h），平台只传
run_id 和小参数。传完整 dict 的旧调用方式仍然兼容。

注意：内存暂存要求单 worker（uvicorn 默认 1）；多 worker 部署需把 _STORE 换 Redis。

启动：
  pip install -r requirements.txt
  SITE_DIAGNOSIS_API_KEY=<与平台一致的key> uvicorn tools.api_server:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

# 让 tools/ 能 import 项目根的 diagnose/render 包
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Security, status
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field

from diagnose import pipeline
from render.build import build_report_from_dict
from render.html import render_html_string

app = FastAPI(
    title="site-diagnosis-zh API",
    description="中文官网 GEO 诊断工具集 — 供 agent 平台工作流调用",
    version="2.0.0",
)


@app.middleware("http")
async def _log_requests(request: Request, call_next):
    """把每个进来的请求打到 uvicorn 控制台，便于排查平台侧 URL 配置。"""
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


# ── run_id 暂存 ──────────────────────────────────────────────
_STORE_TTL = 2 * 3600   # 2 小时过期
_STORE_MAX = 50         # 最多同时保留 50 次诊断
_STORE: dict = {}


def _store_put(run_id: str, data: dict) -> None:
    now = time.time()
    for k in [k for k, (_, ts) in _STORE.items() if now - ts > _STORE_TTL]:
        _STORE.pop(k, None)
    while len(_STORE) >= _STORE_MAX:
        _STORE.pop(min(_STORE, key=lambda k: _STORE[k][1]), None)
    _STORE[run_id] = (data, now)


def _store_get(run_id: str) -> dict:
    item = _STORE.get(run_id)
    if item is None or time.time() - item[1] > _STORE_TTL:
        _STORE.pop(run_id, None)
        raise HTTPException(
            status_code=404,
            detail=f"run_id 「{run_id}」不存在或已过期（2 小时有效）——请重新跑 diagnose_run",
        )
    return item[0]


def _as_json(x: Any) -> Any:
    """平台变量可能是 JSON 字符串 / dict / list / None，统一还原成 Python 对象。"""
    if x is None or isinstance(x, (dict, list)):
        return x
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return None
        try:
            return json.loads(s)
        except Exception:
            return None
    return x


# ── 请求模型 ──────────────────────────────────────────────────

class CollectRequest(BaseModel):
    """{"url": "..."}；也兼容平台网关嵌套 {"inputs": {"url": "..."}}"""
    url: Optional[str] = None
    inputs: Optional[dict] = None
    run_id: Optional[str] = None   # 调用方可指定 run_id，缺省服务端生成

    def resolved_url(self) -> str:
        u = self.url or (self.inputs or {}).get("url") or ""
        if not u:
            raise HTTPException(status_code=422, detail="缺少 url 参数")
        return u


class RescanRequest(BaseModel):
    run_id: Optional[str] = None   # 传入则 fetch_page 从暂存取
    agent_picks: Any = None        # {"articles": [...], "products": [...]}
    fetch_page: Any = None         # 旧方式：直接回传 diagnose_run 的 fetch


class AnalyzeRequest(BaseModel):
    run_id: Optional[str] = None   # 传入则以下大字段全部从暂存取
    url: str = ""
    fetch_page: Any = None
    articles: Any = None
    pages_audit: Any = None
    schema_: Any = Field(default=None, alias="schema")
    citability: Any = None
    bots: Any = None
    technical: Any = None

    model_config = {"populate_by_name": True}


class RenderRequest(BaseModel):
    run_id: Optional[str] = None   # 传入则以下大字段全部从暂存取
    domain: str = ""
    url: str = ""
    scores: Any = None             # LLM 写的评分对象（dict 或 JSON 字符串，可缺省走兜底）
    offline: bool = False
    # 旧方式：完整回传
    fetch: Any = None
    robots: Any = None
    bots: Any = None
    citability: Any = None
    schema_: Any = Field(default=None, alias="schema")
    articles: Any = None
    technical: Any = None
    brand: Any = None
    issues: Any = None
    signals_summary: Any = None

    model_config = {"populate_by_name": True}


# ── 端点 ─────────────────────────────────────────────────────

@app.post("/collect")
@api_router.post("/collect")
def collect(req: CollectRequest, _key: str = Depends(require_api_key)) -> dict:
    """一站式抓取：fetch_core + probe_bots + scan_pages（并发 + 25s 软超时）。

    返回 {url, run_id, fetch, robots, technical, bots, citability,
          articles, pages, schema, _meta}。
    后续 rescan/analyze/render 只需回传 run_id，不必搬运大 JSON。

    _meta.partial=true 时对应子 dict 为 {"error": "..."}，HTTP 仍为 200——
    让 LLM 拿到部分数据继续推理，而不是整个工具调用失败。
    """
    url = req.resolved_url()
    result = pipeline.fetch_all(url, soft_timeout=25.0)
    run_id = (req.run_id or "").strip() or uuid.uuid4().hex[:12]
    _store_put(run_id, {"url": url, **result})
    result["run_id"] = run_id
    return result


@app.post("/rescan")
@api_router.post("/rescan")
def rescan(req: RescanRequest, _key: str = Depends(require_api_key)) -> dict:
    """动态站补抓：LLM 挑完 URL 后回传，重建 articles + schema。

    传 run_id 时 fetch_page 自动从暂存取；也兼容旧方式直接回传 fetch_page。
    返回 {articles, schema}，并已同步写回暂存（后续 analyze/render 直接用）。
    """
    if req.run_id:
        stored = _store_get(req.run_id)
        fetch_page = stored.get("fetch") or {}
    else:
        stored = None
        fetch_page = _as_json(req.fetch_page) or {}

    agent_picks = _as_json(req.agent_picks) or {}
    result = pipeline.rescan_pages(agent_picks, fetch_page)

    if stored is not None:
        stored["articles"] = result.get("articles") or stored.get("articles") or {}
        stored["schema"] = result.get("schema") or stored.get("schema") or {}
        _store_put(req.run_id, stored)
        result["run_id"] = req.run_id
    return result


@app.post("/analyze")
@api_router.post("/analyze")
def analyze(req: AnalyzeRequest, _key: str = Depends(require_api_key)) -> dict:
    """纯计算：signals + analyzers + baseline_scores + brand。

    传 run_id 时所有大字段从暂存取；返回 {issues, signals_summary,
    baseline_scores, brand}，并已写回暂存供 /render 使用。
    """
    if req.run_id:
        stored = _store_get(req.run_id)
        fetch_page = stored.get("fetch") or {}
        articles = stored.get("articles") or {}
        pages_audit = stored.get("pages") or {}
        schema = stored.get("schema") or {}
        citability = stored.get("citability")
        bots = stored.get("bots")
        technical = stored.get("technical")
        url = req.url or stored.get("url", "")
    else:
        fetch_page = _as_json(req.fetch_page) or {}
        articles = _as_json(req.articles) or {}
        pages_audit = _as_json(req.pages_audit) or {}
        schema = _as_json(req.schema_) or {}
        citability = _as_json(req.citability)
        bots = _as_json(req.bots)
        technical = _as_json(req.technical)
        url = req.url

    result = pipeline.analyze(
        fetch_page=fetch_page,
        articles=articles,
        pages_audit=pages_audit,
        schema=schema,
        citability=citability,
        bots=bots,
        technical=technical,
        url=url,
    )

    if req.run_id:
        stored.update({
            "brand": result.get("brand"),
            "issues": result.get("issues"),
            "signals_summary": result.get("signals_summary"),
            "baseline_scores": result.get("baseline_scores"),
        })
        _store_put(req.run_id, stored)
    return result


@app.post("/render")
@api_router.post("/render")
def render(req: RenderRequest, _key: str = Depends(require_api_key)) -> dict:
    """渲染中文 HTML 报告（返回 JSON 包装，便于 agent 平台变量传递）。

    传 run_id 时所有大字段从暂存取，body 只需 {run_id, scores}。
    scores 可缺省——缺省时渲染器用确定性数据生成兜底发现。

    返回 {success, html, size, domain, url, filename, is_fallback, error}。
    """
    if req.run_id:
        stored = _store_get(req.run_id)
        url = req.url or stored.get("url", "")
        data = {
            "fetch": stored.get("fetch") or {},
            "robots": stored.get("robots") or {},
            "bots": stored.get("bots") or {},
            "citability": stored.get("citability") or {},
            "schema": stored.get("schema") or {},
            "brand": stored.get("brand") or {},
            "technical": stored.get("technical") or {},
            "scores": _as_json(req.scores) or {},
            "articles": stored.get("articles") or {},
            "signals": stored.get("signals_summary") or {},
            "issues": stored.get("issues") or {},
        }
    else:
        url = req.url
        data = {
            "fetch": _as_json(req.fetch) or {},
            "robots": _as_json(req.robots) or {},
            "bots": _as_json(req.bots) or {},
            "citability": _as_json(req.citability) or {},
            "schema": _as_json(req.schema_) or {},
            "brand": _as_json(req.brand) or {},
            "technical": _as_json(req.technical) or {},
            "scores": _as_json(req.scores) or {},
            "articles": _as_json(req.articles) or {},
            "signals": _as_json(req.signals_summary) or {},
            "issues": _as_json(req.issues) or {},
        }

    domain = req.domain or urlparse(url).netloc.replace("www.", "").replace(":", "_") or "site"
    safe_domain = "".join(c if c.isalnum() or c in ".-_" else "_" for c in domain) or "site"
    filename = f"{safe_domain}_geo_report.html"

    try:
        report = build_report_from_dict(data, domain=domain, url=url)
        html_str = render_html_string(report, offline=req.offline)
        return {
            "success": True,
            "html": html_str,
            "size": len(html_str),
            "domain": domain,
            "url": url,
            "filename": filename,
            "is_fallback": False,
            "error": None,
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


@app.get("/health")
@api_router.get("/health")
def health() -> dict:
    return {"status": "ok"}


# 所有 api_router 装饰器之后注册，让 /api/* 与 /* 双路径生效
app.include_router(api_router)
