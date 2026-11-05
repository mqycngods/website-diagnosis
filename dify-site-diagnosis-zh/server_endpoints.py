"""site-diagnosis-zh 的 Dify 平台服务端端点（参考实现，Flask Blueprint）。

部署方式（二选一）：
  1. 独立服务：在本仓库根目录 `python server_endpoints.py`（默认 9100 端口）
  2. 并入现有 geo-server：把 bp 注册进主 app

设计要点：
  - 无文件系统会话：采集数据按 run_id 存内存（TTL 2h），Dify 节点间只传紧凑摘要
  - render 走 render/build.py 的 build_report_from_dict（纯内存，无需 work/ 目录）
  - 生成的 HTML/MD 也存在 run_id 下，GET diagnosis_download 可取（浏览器直接下载）
  - 鉴权：Authorization: Bearer $GEO_ACCESS_TOKEN

注意：内存暂存要求单 worker（gunicorn --workers 1），多 worker 需换 Redis。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

from flask import Blueprint, Flask, Response, jsonify, request

from diagnose import pipeline
from render.build import build_report_from_dict

bp = Blueprint("diagnosis", __name__)

TOKEN = os.environ.get("GEO_ACCESS_TOKEN", "")
_STORE: dict = {}
_STORE_TTL = 2 * 3600
_LOCK = threading.Lock()


# ── 基础设施 ─────────────────────────────────────────────────

def _auth():
    auth = request.headers.get("Authorization", "")
    if not TOKEN or auth != f"Bearer {TOKEN}":
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return None


def _run(run_id: str) -> dict:
    """取/建一个 run 槽位，顺手清过期槽。"""
    now = time.time()
    with _LOCK:
        for rid in [k for k, v in _STORE.items() if now - v["ts"] > _STORE_TTL]:
            del _STORE[rid]
        slot = _STORE.setdefault(run_id, {"ts": now, "data": {}, "downloads": {}})
        slot["ts"] = now
        return slot


def _ok(data: dict):
    return jsonify({"ok": True, "error": "", "data": data})


def _fail(msg: str, code: int = 200):
    return jsonify({"ok": False, "error": msg, "data": {}}), code


# ── 摘要压缩（传给 Dify 节点的体积小，全量数据留在服务端） ──────

def _fetch_summary(page):
    if not isinstance(page, dict) or page.get("error"):
        return page or {}
    sd_types = sorted({str(x.get("@type")) for x in (page.get("structured_data") or [])
                       if isinstance(x, dict) and x.get("@type")})
    heads = [h for h in (page.get("heading_structure") or []) if isinstance(h, dict)]
    return {
        "url": page.get("url"), "status_code": page.get("status_code"),
        "title": page.get("title"), "description": page.get("description"),
        "canonical": page.get("canonical"),
        "h1_tags": page.get("h1_tags") or [],
        "h2_sample": [h.get("text") for h in heads if h.get("level") == 2][:20],
        "word_count": page.get("word_count"),
        "has_ssr_content": page.get("has_ssr_content"),
        "challenge_detected": page.get("challenge_detected"),
        "freshness": page.get("freshness"),
        "security_headers": page.get("security_headers"),
        "structured_data_types": sd_types,
        "internal_links_count": len(page.get("internal_links") or []),
        "errors": page.get("errors"),
    }


def _citability_summary(cit):
    if not isinstance(cit, dict) or cit.get("error"):
        return cit or {}

    def _blocks(key):
        out = []
        for b in (cit.get(key) or [])[:5]:
            if isinstance(b, dict):
                out.append({"heading": b.get("heading"), "total_score": b.get("total_score"),
                            "grade": b.get("grade"), "word_count": b.get("word_count"),
                            "breakdown": b.get("breakdown"),
                            "preview": str(b.get("preview") or "")[:150]})
        return out

    return {
        "average_citability_score": cit.get("average_citability_score"),
        "total_blocks_analyzed": cit.get("total_blocks_analyzed"),
        "top_5_citable": _blocks("top_5_citable"),
        "bottom_5_citable": _blocks("bottom_5_citable"),
    }


def _pages_summary(pages):
    if not isinstance(pages, dict) or pages.get("error"):
        return pages or {}
    found = {}
    for ptype, p in (pages.get("found") or {}).items():
        if isinstance(p, dict):
            found[ptype] = {"url": p.get("url"), "status_code": p.get("status_code"),
                            "title": p.get("title"), "note": p.get("note")}
    return {"found": found, "missing": pages.get("missing") or []}


# ── 采集端点 ─────────────────────────────────────────────────

@bp.post("/api/v1/tools/diagnosis_fetch_core")
def fetch_core():
    if (e := _auth()):
        return e
    body = request.get_json(force=True)
    run_id, url = body.get("run_id", ""), body.get("url", "")
    if not run_id or not url:
        return _fail("run_id 与 url 必填")
    out = pipeline.fetch_core(url)  # {fetch, robots, technical}
    _run(run_id)["data"].update(out)
    return _ok({"fetch_summary": _fetch_summary(out.get("fetch")),
                "robots": out.get("robots") or {},
                "technical": out.get("technical") or {}})


@bp.post("/api/v1/tools/diagnosis_probe_bots")
def probe_bots():
    if (e := _auth()):
        return e
    body = request.get_json(force=True)
    run_id, url = body.get("run_id", ""), body.get("url", "")
    if not run_id or not url:
        return _fail("run_id 与 url 必填")
    out = pipeline.probe_bots(url)  # {bots, citability}
    _run(run_id)["data"].update(out)
    return _ok({"bots": out.get("bots") or {},
                "citability_summary": _citability_summary(out.get("citability"))})


@bp.post("/api/v1/tools/diagnosis_scan_pages")
def scan_pages():
    if (e := _auth()):
        return e
    body = request.get_json(force=True)
    run_id, url = body.get("run_id", ""), body.get("url", "")
    if not run_id or not url:
        return _fail("run_id 与 url 必填")
    st = _run(run_id)["data"]
    out = pipeline.scan_pages(url, st.get("fetch") or {})  # {articles, pages, schema}
    st.update(out)
    arts = out.get("articles") or {}
    links = [{"text": str(l.get("text") or "")[:80], "url": l.get("url")}
             for l in (arts.get("internal_links") or [])[:120] if isinstance(l, dict)]
    return _ok({"internal_links_json": json.dumps(links, ensure_ascii=False),
                "sampled_articles": arts.get("sampled") or [],
                "sampled_products": arts.get("sampled_products") or [],
                "pages_summary": _pages_summary(out.get("pages")),
                "schema": out.get("schema") or {}})


@bp.post("/api/v1/tools/diagnosis_rescan_pages")
def rescan_pages():
    if (e := _auth()):
        return e
    body = request.get_json(force=True)
    run_id, picks = body.get("run_id", ""), body.get("agent_picks") or {}
    if not run_id:
        return _fail("run_id 必填")
    st = _run(run_id)["data"]
    if not st.get("fetch"):
        return _fail("该 run_id 尚未执行 fetch_core")
    out = pipeline.rescan_pages(picks, st["fetch"])  # {articles, schema}
    st.update(out)
    arts = out.get("articles") or {}
    return _ok({"schema": out.get("schema") or {},
                "sampled_articles": arts.get("sampled") or [],
                "sampled_products": arts.get("sampled_products") or []})


@bp.post("/api/v1/tools/diagnosis_analyze")
def analyze():
    if (e := _auth()):
        return e
    body = request.get_json(force=True)
    run_id, url = body.get("run_id", ""), body.get("url", "")
    if not run_id:
        return _fail("run_id 必填")
    st = _run(run_id)["data"]
    out = pipeline.analyze(
        fetch_page=st.get("fetch") or {}, articles=st.get("articles") or {},
        pages_audit=st.get("pages") or {}, schema=st.get("schema") or {},
        citability=st.get("citability") or {}, bots=st.get("bots") or {},
        technical=st.get("technical") or {}, url=url)
    st.update(out)  # brand / issues / signals_summary / baseline_scores
    return _ok({"brand": out.get("brand") or {}, "issues": out.get("issues") or {},
                "signals_summary": out.get("signals_summary") or {},
                "baseline_scores": out.get("baseline_scores") or {}})


@bp.post("/api/v1/tools/diagnosis_save_scores")
def save_scores():
    if (e := _auth()):
        return e
    body = request.get_json(force=True)
    run_id, scores = body.get("run_id", ""), body.get("scores")
    if not run_id or not isinstance(scores, dict):
        return _fail("run_id 必填且 scores 必须是对象")
    _run(run_id)["data"]["scores"] = scores
    return _ok({"saved": True})


@bp.post("/api/v1/tools/diagnosis_render")
def render():
    if (e := _auth()):
        return e
    body = request.get_json(force=True)
    run_id, url, domain = body.get("run_id", ""), body.get("url", ""), body.get("domain", "")
    if not run_id or not url:
        return _fail("run_id 与 url 必填")
    st = _run(run_id)
    data = {k: st["data"].get(k) or {} for k in (
        "fetch", "robots", "bots", "citability", "schema", "brand",
        "technical", "scores", "articles", "signals", "issues")}
    try:
        report = build_report_from_dict(data, domain=domain, url=url)
        from render.html import render_html
        from render.markdown import render_markdown
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            html_path = Path(f.name)
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
            md_path = Path(f.name)
        render_html(report, html_path)
        render_markdown(report, md_path)
        html = html_path.read_text(encoding="utf-8")
        md = md_path.read_text(encoding="utf-8")
        html_path.unlink(missing_ok=True)
        md_path.unlink(missing_ok=True)
    except Exception as e:
        return _fail(f"渲染失败：{type(e).__name__}: {e}")

    rd = report.to_dict()
    st["downloads"] = {"html": html, "md": md}
    base = request.host_url.rstrip("/")
    return _ok({
        "geo_score": rd.get("geo_score", 0), "label_zh": rd.get("label_zh", ""),
        "report_html": html, "report_markdown": md,
        "download_url_html": f"{base}/api/v1/tools/diagnosis_download?run_id={run_id}&fmt=html&token={TOKEN}",
        "download_url_md": f"{base}/api/v1/tools/diagnosis_download?run_id={run_id}&fmt=md&token={TOKEN}",
    })


@bp.get("/api/v1/tools/diagnosis_download")
def download():
    # 浏览器地址栏没法带 Header，这里用 query token 鉴权
    if not TOKEN or request.args.get("token") != TOKEN:
        return _fail("unauthorized", 401)
    run_id, fmt = request.args.get("run_id", ""), request.args.get("fmt", "html")
    slot = _STORE.get(run_id)
    content = (slot or {}).get("downloads", {}).get(fmt)
    if not content:
        return _fail("内容不存在或已过期（内存 TTL 2h）", 404)
    domain = run_id  # 文件名兜底
    mime = "text/html" if fmt == "html" else "text/markdown"
    return Response(content, mimetype=f"{mime}; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="geo-report-{domain}.{fmt}"'})


if __name__ == "__main__":
    app = Flask(__name__)
    app.register_blueprint(bp)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "9100")), threaded=True)
