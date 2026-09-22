"""AI 爬虫 UA 重放实测 — 经验性补充 robots.txt 声明（声明 ≠ 实际）。

先抓浏览器基线，再以每个爬虫 UA 重放，对比状态码 / 内容相似度，
识别"robots 放行但 WAF 静默拦截"的不一致。
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import urlparse

from .crawlers import AI_CRAWLERS, BOT_CLASSES
from .http_utils import get, is_challenge_page

_WAF_PRODUCTS = (
    ("Cloudflare", lambda h, c: "cf-ray" in h or "cloudflare" in h.get("server", "")),
    ("AWS CloudFront", lambda h, c: "x-amz-cf-id" in h),
    ("Akamai", lambda h, c: "akamai" in h.get("server", "") or "akamaighost" in h.get("server", "")),
    ("Sucuri", lambda h, c: "sucuri" in h.get("server", "")),
    ("Imperva Incapsula", lambda h, c: "x-iinfo" in h or "incap_ses" in c),
    ("Fastly", lambda h, c: "fastly" in h.get("server", "") or "x-fastly-request-id" in h),
)


def _detect_waf(resp) -> list:
    headers = {k.lower(): str(v).lower() for k, v in resp.headers.items()}
    cookies = headers.get("set-cookie", "")
    found = []
    for product, pred in _WAF_PRODUCTS:
        try:
            if pred(headers, cookies) and product not in found:
                found.append(product)
        except Exception:
            pass
    return found


def _similarity(a: str, b: str) -> float:
    norm = lambda t: re.sub(r"\s+", " ", (t or "")[:10000]).strip().lower()
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def probe_bots(url: str, timeout: int = 15) -> dict:
    result = {
        "url": url, "baseline": {}, "wafs_detected": [], "probes": [],
        "class_scores": {}, "verdict": "", "overall_score": None, "errors": [],
    }
    if urlparse(url).scheme not in ("http", "https"):
        result["errors"].append("不支持的协议")
        return result

    try:
        base = get(url, timeout=timeout)
    except Exception as e:
        result["errors"].append(f"基线抓取失败：{e}")
        return result

    base_html = base.text or ""
    result["baseline"] = {"status": base.status_code, "length": len(base_html)}
    result["wafs_detected"] = [{"product": p} for p in _detect_waf(base)]

    for name, meta in AI_CRAWLERS.items():
        probe = {"bot": name, "class": meta["class"], "operator": meta["operator"],
                 "status": None, "similarity": None, "blocked": False,
                 "payment_required": False, "block_reason": None}
        try:
            r = get(url, timeout=timeout, headers={"User-Agent": meta["ua"]})
            body = r.text or ""
            probe["status"] = r.status_code
            if r.status_code == 402:
                probe["payment_required"] = True
                probe["block_reason"] = "payment-required"
            elif r.status_code in (403, 406, 429, 503):
                probe["blocked"] = True
                probe["block_reason"] = f"http_{r.status_code}"
            elif is_challenge_page(body, r.status_code):
                probe["blocked"] = True
                probe["block_reason"] = "challenge_page"
            else:
                sim = _similarity(base_html, body)
                probe["similarity"] = round(sim, 3)
                ratio = len(body) / result["baseline"]["length"] if result["baseline"]["length"] else 1.0
                if sim < 0.4 and ratio < 0.5:
                    probe["blocked"] = True
                    probe["block_reason"] = "content_stripped"
        except Exception as e:
            probe["blocked"] = True
            probe["block_reason"] = f"request_error: {e}"
        result["probes"].append(probe)

    # 分类得分：每类 100 - 100*受阻占比；总分为检索0.5/传统0.35/训练0.15 加权
    by_class = {c: [] for c in BOT_CLASSES}
    for p in result["probes"]:
        by_class[p["class"]].append(p)
    for cls in BOT_CLASSES:
        probes = by_class[cls]
        if probes:
            blocked = sum(1 for p in probes if p["blocked"])
            result["class_scores"][cls] = max(0, round(100 * (1 - blocked / len(probes))))
        else:
            result["class_scores"][cls] = None

    retrieval = [s for c in ("live-retrieval", "search-index")
                 for s in [result["class_scores"].get(c)] if s is not None]
    retrieval = sum(retrieval) // len(retrieval) if retrieval else None
    traditional = result["class_scores"].get("traditional-search")
    training = result["class_scores"].get("training")

    def meets(v, t):
        return v is None or v >= t

    if retrieval is None:
        verdict = "INCONCLUSIVE"
    elif meets(retrieval, 90) and meets(traditional, 90):
        verdict = "OPEN" if meets(training, 70) else "HEALTHY_PUBLISHER"
    elif meets(retrieval, 70) and meets(traditional, 70):
        verdict = "PARTIALLY_BLOCKED"
    elif (retrieval or 0) >= 40 or (traditional or 0) >= 40:
        verdict = "MOSTLY_BLOCKED"
    else:
        verdict = "BLOCKED"
    result["verdict"] = verdict

    weighted = [(retrieval, 0.5), (traditional, 0.35), (training, 0.15)]
    wsum = sum(w for s, w in weighted if s is not None)
    result["overall_score"] = (
        round(sum(s * w for s, w in weighted if s is not None) / wsum) if wsum else None
    )
    return result
