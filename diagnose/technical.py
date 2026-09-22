"""技术层 — 安全响应头矩阵 + HTTPS + llms.txt 状态（非计分信号）。"""
from __future__ import annotations

from urllib.parse import urlparse

from .http_utils import get

HEADERS = [
    ("Strict-Transport-Security", "HSTS", 10),
    ("Content-Security-Policy", "CSP", 10),
    ("X-Frame-Options", "X-Frame-Options", 5),
    ("X-Content-Type-Options", "X-Content-Type-Options", 5),
    ("Referrer-Policy", "Referrer-Policy", 5),
    ("Permissions-Policy", "Permissions-Policy", 3),
]


def check_technical(url: str, security_headers: dict = None, timeout: int = 10) -> dict:
    """输入已抓取的 security_headers（fetch.json 提供），评估技术层。"""
    parsed = urlparse(url)
    https = parsed.scheme == "https"

    matrix = []
    earned = 0
    for header, label, pts in HEADERS:
        present = bool((security_headers or {}).get(header))
        matrix.append({"header": label, "present": present, "points": pts,
                       "value": (security_headers or {}).get(header) or ""})
        if present:
            earned += pts
    # HTTPS 基础 30 分
    https_pts = 30 if https else 0
    # 安全头得分（满分 38）+ HTTPS（30），归一化到 100
    sec_score = round((https_pts + earned) / (30 + sum(p for _, _, p in HEADERS)) * 100)

    # llms.txt 状态（非计分）——必须检查是否返回 text/plain 且未重定向回首页/HTML
    base = f"{parsed.scheme}://{parsed.netloc}"
    llms = {"llms_txt": False, "llms_full_txt": False}
    for key, path in (("llms_txt", "/llms.txt"), ("llms_full_txt", "/llms-full.txt")):
        try:
            r = get(base + path, timeout=timeout)
            # 判断有效性：状态码 200，且最终 URL 未重定向离开 /llms，且不是 HTML 响应（如全站 404/SPA 兜底首页）
            final_path = urlparse(r.url).path.lower().rstrip("/")
            expected_path = path.lower().rstrip("/")
            ct = r.headers.get("Content-Type", "").lower()
            is_html = "text/html" in ct or "<!doctype" in r.text[:200].lower() or "<html" in r.text[:200].lower()
            if r.status_code == 200 and final_path == expected_path and not is_html:
                llms[key] = True
        except Exception:
            pass

    return {
        "https": https,
        "security_matrix": matrix,
        "security_score": sec_score,
        "headers_present": sum(1 for m in matrix if m["present"]),
        "headers_total": len(matrix),
        "llms": llms,
    }
