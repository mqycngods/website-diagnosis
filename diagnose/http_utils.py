"""共享 HTTP / 解析工具。"""
from __future__ import annotations

import requests

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
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


def get(url: str, timeout: int = 30, headers: dict = None):
    h = dict(DEFAULT_HEADERS)
    if headers:
        h.update(headers)
    resp = requests.get(url, headers=h, timeout=timeout, allow_redirects=True)
    # requests 默认按 HTTP 头猜编码，缺失时回落 ISO-8859-1，会把中文页面解码成乱码。
    # 服务器未声明 charset 时按 UTF-8 处理（Web 标准默认）。
    if not resp.encoding or resp.encoding.lower() in ("iso-8859-1", "latin-1"):
        resp.encoding = "utf-8"
    return resp


def is_challenge_page(html: str, status_code: int) -> bool:
    head = (html or "")[:8000].lower()
    if status_code in (403, 503):
        if any(m in head for m in _CHALLENGE_MARKERS) or "cloudflare" in head:
            return True
    if status_code == 200 and any(m in head for m in _CHALLENGE_MARKERS):
        return True
    return False
