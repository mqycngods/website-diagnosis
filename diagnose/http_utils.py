"""共享 HTTP / 解析工具。"""
from __future__ import annotations

import gzip
import io
import json
import subprocess
from dataclasses import dataclass
from typing import Dict, Optional

import requests

from .cache import CachedResponse

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; "
        "GPTBot/1.4; +https://openai.com/gptbot)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}

# Cloudflare 等 WAF 挑战页特征
_CHALLENGE_MARKERS = (
    "cf-challenge", "cf-turnstile", "ray id", "checking your browser",
    "just a moment", "enable javascript and cookies", "attention required",
)

# 进程级缓存：report.py collect 启动时装配（MemoryCache 或 FileCache）。
# None = 不缓存（单脚本直跑时的默认行为，保持简单可预测）。
_CACHE = None

# 使用 curl 子进程替代 Python requests，原因：Cloudflare Bot Management
# 会根据 TLS/JA3 指纹 + HTTP/2 指纹拦截自动化工具；Python requests 的指纹
# 即使换 UA 也会被识别为「自动化伪装 UA」并挑战。curl 的指纹更接近真实
# 浏览器（特别是带完整 Accept/Accept-Language/Accept-Encoding 时），可拿到
# 真实 HTML。可通过环境变量 GEO_HTTP_BACKEND=requests 回退到 requests。
import os
_BACKEND = os.environ.get("GEO_HTTP_BACKEND", "curl")


def set_cache(cache):
    """装配缓存（diagnose.cache.MemoryCache / FileCache）。"""
    global _CACHE
    _CACHE = cache


@dataclass
class _CurlResponse:
    """最小化模拟 requests.Response 接口，供下游使用。"""
    status_code: int
    text: str
    content: bytes
    headers: Dict[str, str]
    url: str
    encoding: str = "utf-8"
    ok: bool = True

    def json(self):
        return json.loads(self.text)


def _curl_get(url: str, timeout: int, headers: dict) -> _CurlResponse:
    """用 curl 子进程发请求。带 --compressed 自动解压 gzip。

    用 -D/-o 把响应头与 body 分别写到临时文件，避免 capture_output=True
    时大响应被截断的问题。
    """
    import tempfile
    body_path = tempfile.NamedTemporaryFile(delete=False, suffix=".body").name
    headers_path = tempfile.NamedTemporaryFile(delete=False, suffix=".hdr").name
    meta_path = tempfile.NamedTemporaryFile(delete=False, suffix=".meta").name
    cmd = [
        "curl", "-s", "-S", "-L", "--compressed", "--max-time", str(timeout),
        "-H", f"User-Agent: {headers.get('User-Agent', DEFAULT_HEADERS['User-Agent'])}",
        "-H", f"Accept: {headers.get('Accept', DEFAULT_HEADERS['Accept'])}",
        "-H", f"Accept-Language: {headers.get('Accept-Language', DEFAULT_HEADERS['Accept-Language'])}",
        "-H", "Accept-Encoding: gzip, deflate",
        "-H", "Connection: keep-alive",
        "-H", "Upgrade-Insecure-Requests: 1",
        "-D", headers[":__headers_path__"] if ":__headers_path__" in headers else headers_path,
        "-o", body_path,
        "-w", "%{http_code}|%{url_effective}",
        url,
    ]
    # 其他自定义 headers（如 robots UA 重放时 caller 传的 UA）
    skip_keys = {"user-agent", "accept", "accept-language", "accept-encoding",
                 "connection", "upgrade-insecure-requests"}
    for k, v in headers.items():
        if k.startswith(":"):
            continue
        if k.lower() in skip_keys:
            continue
        cmd.insert(-6, "-H")
        cmd.insert(-6, f"{k}: {v}")
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
    meta = proc.stdout.decode("utf-8", errors="ignore").strip()
    status_code = 0
    final_url = url
    if "|" in meta:
        s, u = meta.split("|", 1)
        try:
            status_code = int(s)
        except Exception:
            pass
        final_url = u or url
    try:
        with open(body_path, "rb") as f:
            body = f.read()
    except Exception:
        body = b""
    try:
        with open(headers_path, "r", encoding="utf-8", errors="ignore") as f:
            raw_headers = f.read()
    except Exception:
        raw_headers = ""
    # 解析响应头（可能有多个 HTTP/1.1 200 块，取最后一个）
    resp_headers: Dict[str, str] = {}
    blocks = raw_headers.split("\r\n\r\n")
    if blocks:
        last = blocks[-1]
        for line in last.splitlines():
            if ":" in line and not line.startswith("HTTP/"):
                k, v = line.split(":", 1)
                resp_headers[k.strip()] = v.strip()
    # 解 gzip 兜底（--compressed 通常已处理）
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:
        try:
            text = gzip.decompress(body).decode("utf-8", errors="replace")
        except Exception:
            text = body.decode("latin-1", errors="replace")
    # 清理临时文件
    for p in (body_path, headers_path):
        try:
            os.unlink(p)
        except Exception:
            pass
    return _CurlResponse(
        status_code=status_code,
        text=text,
        content=body,
        headers=resp_headers,
        url=final_url,
        ok=(200 <= status_code < 400),
    )


def get(url: str, timeout: int = 30, headers: dict = None):
    h = dict(DEFAULT_HEADERS)
    if headers:
        h.update(headers)
    ua = h.get("User-Agent", "")

    if _CACHE is not None:
        hit = _CACHE.get(url, ua)
        if hit is not None:
            return hit

    if _BACKEND == "curl":
        try:
            resp = _curl_get(url, timeout, h)
        except Exception:
            # curl 失败时回落 requests
            resp = requests.get(url, headers=h, timeout=timeout, allow_redirects=True)
            if not resp.encoding or resp.encoding.lower() in ("iso-8859-1", "latin-1"):
                resp.encoding = "utf-8"
    else:
        resp = requests.get(url, headers=h, timeout=timeout, allow_redirects=True)
        # requests 默认按 HTTP 头猜编码，缺失时回落 ISO-8859-1，会把中文页面解码成乱码。
        # 服务器未声明 charset 时按 UTF-8 处理（Web 标准默认）。
        if not resp.encoding or resp.encoding.lower() in ("iso-8859-1", "latin-1"):
            resp.encoding = "utf-8"

    if _CACHE is not None:
        # 统一转成 CachedResponse 存入
        if isinstance(resp, _CurlResponse):
            cached = CachedResponse(
                status_code=resp.status_code,
                headers=dict(resp.headers or {}),
                text=resp.text or "",
                url=resp.url or url,
            )
        else:
            cached = CachedResponse.from_response(resp)
        _CACHE.set(url, ua, cached)
        return cached
    return resp


def is_challenge_page(html: str, status_code: int) -> bool:
    head = (html or "")[:8000].lower()
    if status_code in (403, 503):
        if any(m in head for m in _CHALLENGE_MARKERS) or "cloudflare" in head:
            return True
    if status_code == 200 and any(m in head for m in _CHALLENGE_MARKERS):
        return True
    return False
