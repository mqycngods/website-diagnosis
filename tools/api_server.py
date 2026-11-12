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

注意：内存暂存要求单 worker（uvicorn 默认 1）；多 worker 部署需把 _STORE 换 Redis。
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
from render.markdown import render_markdown

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
def _parse_body(raw: bytes) -> dict:
    """平台模板拼出的 body 可能不是合法 JSON（单引号 dict、变量为空等）。

    分层兜底：json.loads → ast.literal_eval（单引号 Python dict）→
    单引号换双引号再试 → 全失败返回 {}，绝不 422 json_invalid。
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
        return {}


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
    """把 HTML / Markdown / 原始数据落盘到 results/<domain>-<ts>/。

    plan_md（GEO 执行清单 Markdown）非空时单独落盘 <safe>-geo-plan.md，
    返回的 md_name 指向清单文件（md_url 即清单在线链接）；否则 md_name
    仍是诊断报告 md。
    """
    safe = _NAME_SAFE.sub("_", domain) or "site"
    dir_name = f"{safe}-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir = RESULTS_DIR / dir_name
    out_dir.mkdir(parents=True, exist_ok=True)

    html_name = f"{safe}-geo-report.html"
    md_name = f"{safe}-geo-report.md"
    (out_dir / html_name).write_text(html_str, encoding="utf-8")
    render_markdown(report, out_dir / md_name)
    if plan_md.strip():
        plan_name = f"{safe}-geo-plan.md"
        (out_dir / plan_name).write_text(plan_md, encoding="utf-8")
        md_name = plan_name
    (out_dir / "report-data.json").write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, default=str), encoding="utf-8")
    return {"dir_name": dir_name, "html_name": html_name, "md_name": md_name}


# ── 端点 ─────────────────────────────────────────────────────

@app.post("/collect")
@api_router.post("/collect")
async def collect(request: Request, _key: str = Depends(require_api_key)):
    """一站式抓取：fetch_core + probe_bots + scan_pages（并发 + 25s 软超时）。

    返回 {url, run_id, fetch, robots, technical, bots, citability,
          articles, pages, schema, _meta}。
    后续 rescan/analyze/render 只需回传 run_id，不必搬运大 JSON。
    """
    body = _parse_body(await request.body())
    url = (body.get("url") or (body.get("inputs") or {}).get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=422, detail="缺少 url 参数")

    result = await run_in_threadpool(pipeline.fetch_all, url, 60.0)
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
    run_id = str(body.get("run_id") or "").strip()
    if run_id:
        stored = _store_get(run_id)
        fetch_page = stored.get("fetch") or {}
    else:
        stored = None
        fetch_page = _as_json(body.get("fetch_page")) or {}

    agent_picks = _as_json(body.get("agent_picks")) or {}
    result = await run_in_threadpool(pipeline.rescan_pages, agent_picks, fetch_page)

    if stored is not None:
        stored["articles"] = result.get("articles") or stored.get("articles") or {}
        stored["schema"] = result.get("schema") or stored.get("schema") or {}
        _store_put(run_id, stored)
        result["run_id"] = run_id
    return result


@app.post("/analyze")
@api_router.post("/analyze")
async def analyze(request: Request, _key: str = Depends(require_api_key)):
    """纯计算：signals + analyzers + baseline_scores + brand。

    传 run_id 时所有大字段从暂存取；返回 {issues, signals_summary,
    baseline_scores, brand}，并已写回暂存供 /render 使用。
    """
    body = _parse_body(await request.body())
    run_id = str(body.get("run_id") or "").strip()
    if run_id:
        stored = _store_get(run_id)
        fetch_page = stored.get("fetch") or {}
        articles = stored.get("articles") or {}
        pages_audit = stored.get("pages") or {}
        schema = stored.get("schema") or {}
        citability = stored.get("citability")
        bots = stored.get("bots")
        technical = stored.get("technical")
        url = body.get("url") or stored.get("url", "")
    else:
        fetch_page = _as_json(body.get("fetch_page")) or {}
        articles = _as_json(body.get("articles")) or {}
        pages_audit = _as_json(body.get("pages_audit")) or {}
        schema = _as_json(body.get("schema")) or {}
        citability = _as_json(body.get("citability"))
        bots = _as_json(body.get("bots"))
        technical = _as_json(body.get("technical"))
        url = body.get("url", "")

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
    )

    if run_id:
        stored.update({
            "brand": result.get("brand"),
            "issues": result.get("issues"),
            "signals_summary": result.get("signals_summary"),
            "baseline_scores": result.get("baseline_scores"),
        })
        _store_put(run_id, stored)
    return result


@app.post("/render")
@api_router.post("/render")
async def render(request: Request, _key: str = Depends(require_api_key)):
    """渲染中文 HTML 报告，落盘 results/，并返回在线访问链接。

    传 run_id 时所有大字段从暂存取，body 只需 {run_id, scores}。
    scores 可缺省/格式不合法——缺省时渲染器用确定性数据生成兜底发现。
    plan_md 可缺省：传了 GEO 执行清单 Markdown 时，服务端把它落盘为
    <domain>-geo-plan.md，且 md_url 指向该清单链接；缺省则 md_url 仍是
    诊断报告 md。

    返回 {success, html, size, domain, url, filename, result_dir,
          report_path, report_url, md_path, md_url, is_fallback, error}。
    report_url 可直接浏览器打开，md_url 是 Markdown 纯文本预览。
    """
    body = _parse_body(await request.body())
    run_id = str(body.get("run_id") or "").strip()
    scores = _as_json(body.get("scores")) or {}
    plan_md = str(body.get("plan_md") or "")
    offline = bool(body.get("offline", False))

    if run_id:
        stored = _store_get(run_id)
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
        }

    domain = str(body.get("domain") or "") or \
        urlparse(url).netloc.replace("www.", "").replace(":", "_") or "site"
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
            "md_path": f"{prefix}/{saved['md_name']}",
            "md_url": f"{base}{prefix}/{saved['md_name']}",
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
