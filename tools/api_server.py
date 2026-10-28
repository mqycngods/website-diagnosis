"""FastAPI 服务：把 diagnose/render 包成 agent 平台的工具端点。

端点设计贴合「平台工具化」工作流：
  POST /collect  → 抓取所有确定性事实（fetch_core + probe_bots + scan_pages）
  POST /rescan   → 动态站兜底：LLM 挑 URL 后补抓（pending_agent_pick 分支）
  POST /analyze  → 事实汇总成 signals/issues/baseline，喂给 LLM 评分
  POST /render   → LLM 写完 scores 后渲染 HTML 报告

所有端点无状态，dict 进 dict 出，平台自行传递中间变量。

启动：
 python3 -m pip install fastapi uvicorn
  uvicorn tools.api_server:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

# 让 tools/ 能 import 项目根的 diagnose/render 包
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Security, status
from fastapi.responses import HTMLResponse
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field

from diagnose import pipeline
from render.build import build_report_from_dict
from render.html import render_html_string

app = FastAPI(
    title="site-diagnosis-zh API",
    description="中文官网 GEO 诊断工具集 — 供 agent 平台工作流调用",
    version="1.0.0",
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
# 临时跑通阶段的轻量鉴权：即使 tunnel URL 泄露，没 key 也调不动。
# key 从环境变量 SITE_DIAGNOSIS_API_KEY 读，缺省时用下面的临时值（仅调试用，生产必改）。
import os
_API_KEY = os.environ.get("SITE_DIAGNOSIS_API_KEY", "dev-key-please-change-me")
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(key: str = Security(_api_key_header)):
    if not key or key != _API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或缺失 X-API-Key 请求头",
        )
    return key


# ── 请求模型 ──────────────────────────────────────────────────

class CollectRequest(BaseModel):
    """同时兼容两种 body 形状：
      {"url": "..."}                  — 直连
      {"inputs": {"url": "..."}}      — 平台网关嵌套
    """
    url: Optional[str] = None
    inputs: Optional[dict] = None

    def resolved_url(self) -> str:
        u = self.url or (self.inputs or {}).get("url") or ""
        if not u:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="缺少 url 参数")
        return u


class RescanRequest(BaseModel):
    """pending_agent_pick 分支：LLM 挑完 URL 后回传补抓。

    兼容两种入参形态：
      - dict 直接传（平台支持对象变量）
      - JSON 字符串传（平台只支持字符串变量，这里二次解析）
    """
    agent_picks: Any          # {"articles": [...], "products": [...]}
    fetch_page: Any           # /collect 返回的 fetch 结果

    def parsed_agent_picks(self) -> dict:
        if isinstance(self.agent_picks, str):
            import json
            return json.loads(self.agent_picks)
        return self.agent_picks if isinstance(self.agent_picks, dict) else {}

    def parsed_fetch_page(self) -> dict:
        if isinstance(self.fetch_page, str):
            import json
            return json.loads(self.fetch_page)
        return self.fetch_page if isinstance(self.fetch_page, dict) else {}


class AnalyzeRequest(BaseModel):
    """把 /collect 的三段结果原样回传，零网络纯计算。"""
    url: str = ""
    fetch_page: dict
    articles: dict
    pages_audit: dict
    schema_: dict = Field(alias="schema")
    citability: Optional[dict] = None
    bots: Optional[dict] = None
    technical: Optional[dict] = None

    class Config:
        populate_by_name = True


class RenderRequest(BaseModel):
    """LLM 写完 scores 后，把所有中间数据打包传给 render。"""
    domain: str
    url: str
    audit_date: Optional[str] = None
    # 来自 /collect
    fetch: dict = {}
    robots: dict = {}
    bots: dict = {}
    citability: dict = {}
    schema_: dict = Field(default={}, alias="schema")
    articles: dict = {}
    technical: dict = {}
    # 来自 /analyze
    brand: dict = {}
    issues: dict = {}
    signals_summary: dict = {}     # 渲染器用 summary 即可（长文本由 CSS 截断）
    # 来自 LLM 评分节点（可缺，缺了走兜底发现）
    scores: dict = {}
    # 渲染选项
    offline: bool = False          # True 时内联 echarts（单文件，可离线打开）

    class Config:
        populate_by_name = True


# ── 端点 ─────────────────────────────────────────────────────

@app.post("/collect")
@api_router.post("/collect")
def collect(req: CollectRequest, _key: str = Depends(require_api_key)) -> dict:
    """一站式抓取：fetch_core + probe_bots + scan_pages（并发 + 25s 兜底）。

    返回紧凑结构，平台按需取用：
      {url, fetch, robots, technical, bots, citability, articles, pages, schema, _meta}

    并发编排：
      - 阶段 1: fetch_core（fetch_page 与 robots 并发）
      - 阶段 2: probe_bots 与 scan_pages 并发（scan_pages 复用阶段 1 的 fetch）

    _meta 字段：
      - elapsed: 实际耗时（秒）
      - partial: true 表示有子任务超时被截断
      - completed: 已完成的子任务列表
      - timed_out: 超时的子任务列表
    当 partial=true 时，对应子 dict 为 {"error": "..."}，但 HTTP 状态仍为 200——
    让 LLM 至少能拿到部分数据继续推理，而不是整个工具调用失败。

    若 articles.pending_agent_pick == true（动态站正则失效），
    平台 LLM 节点从 articles.internal_links 挑 URL 后调 /rescan 补抓。
    """
    url = req.resolved_url()
    return pipeline.fetch_all(url, soft_timeout=25.0)


@app.post("/rescan")
@api_router.post("/rescan")
async def rescan(request: Request, _key: str = Depends(require_api_key)) -> dict:
    """动态站补抓：LLM 挑完 URL 后回传，重建 articles + schema。

    兼容两种调用方式：
      1. Body JSON: {"agent_picks": {...}, "fetch_page": {...}}
      2. Query 参数: ?agent_picks={...}&fetch_page={...}（URL 编码的 JSON 字符串）
    """
    import json
    from urllib.parse import unquote

    # 优先从 query 参数读（某些平台只支持 URL 变量）
    agent_picks_str = request.query_params.get("agent_picks")
    fetch_page_str = request.query_params.get("fetch_page")

    if agent_picks_str and fetch_page_str:
        agent_picks = json.loads(unquote(agent_picks_str))
        fetch_page = json.loads(unquote(fetch_page_str))
    else:
        # 否则从 body 读
        body = await request.json()
        agent_picks = body.get("agent_picks", {})
        fetch_page = body.get("fetch_page", {})
        # 兼容字符串形态
        if isinstance(agent_picks, str):
            agent_picks = json.loads(agent_picks)
        if isinstance(fetch_page, str):
            fetch_page = json.loads(fetch_page)

    return pipeline.rescan_pages(agent_picks, fetch_page)


@app.post("/analyze")
@api_router.post("/analyze")
def analyze(req: AnalyzeRequest, _key: str = Depends(require_api_key)) -> dict:
    """纯计算：signals + analyzers + baseline_scores + brand。

    返回 {issues, signals_summary, baseline_scores, brand}。
    signals_summary 是裁剪版（长文本截断到 200 字），供 LLM 评分用；
    完整 signals 不返回（太大且 LLM 不需要），/render 时用 summary 即可。
    """
    return pipeline.analyze(
        fetch_page=req.fetch_page,
        articles=req.articles,
        pages_audit=req.pages_audit,
        schema=req.schema_,
        citability=req.citability,
        bots=req.bots,
        technical=req.technical,
        url=req.url,
    )


@app.post("/render")
@api_router.post("/render")
def render(req: RenderRequest, _key: str = Depends(require_api_key)) -> dict:
    """渲染中文 HTML 报告（返回 JSON 包装，便于 agent 平台变量传递）。

    直接把 /collect 与 /analyze 的输出、加上 LLM 写的 scores 打包传入。
    scores 可缺——缺了渲染器用确定性数据生成兜底发现。

    返回 JSON 结构：
      {
        "success": bool,
        "html": str,           # 原始 HTML 字符串（平台变量直接取用，无需反转义）
        "size": int,           # HTML 字符数
        "domain": str,
        "url": str,
        "filename": str,       # 建议保存的文件名，如 "example.com_geo_report.html"
        "is_fallback": bool,   # true 表示正常渲染失败，返回的是降级版报告
        "error": str | None    # 降级原因
      }

    平台后续节点用法：
      - 代码节点 / 变量提取：直接读 result.html
      - LLM 节点（若需输出）：直接引用变量 {{ render.html }}，无需 Prompt 转义
    """
    data = {
        "fetch": req.fetch,
        "robots": req.robots,
        "bots": req.bots,
        "citability": req.citability,
        "schema": req.schema_,
        "brand": req.brand,
        "technical": req.technical,
        "scores": req.scores,
        "articles": req.articles,
        "signals": req.signals_summary,
        "issues": req.issues,
    }
    safe_domain = "".join(c if c.isalnum() or c in ".-_" else "_" for c in req.domain) or "site"
    filename = f"{safe_domain}_geo_report.html"

    try:
        report = build_report_from_dict(
            data, domain=req.domain, url=req.url, audit_date=req.audit_date)
        html_str = render_html_string(report, offline=req.offline)
        return {
            "success": True,
            "html": html_str,
            "size": len(html_str),
            "domain": req.domain,
            "url": req.url,
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
  <title>{req.domain} - GEO 诊断报告（降级版）</title>
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
    <p><strong>站点：</strong>{req.url}</p>
    <p><strong>错误：</strong>{str(e)}</p>
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
            "domain": req.domain,
            "url": req.url,
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
