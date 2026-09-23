"""解析 robots.txt 的 AI 爬虫声明策略 + sitemap 可达性。"""
from __future__ import annotations

from urllib.parse import urlparse

from .crawlers import AI_CRAWLERS
from .http_utils import get
from .sitemap import parse_sitemap_urls as _parse_sitemap_urls


def _analyze_sitemap(base_url: str, robots_sitemaps: list, timeout: int = 10) -> dict:
    """sitemap 可达性 + 内容分析。

    返回 {declared_in_robots, declared_urls, reachable, url_count, latest_lastmod, path_hints, error}
    """
    result = {
        "declared_in_robots": bool(robots_sitemaps),
        "declared_urls": robots_sitemaps or [],
        "reachable": False,
        "url_count": 0,
        "latest_lastmod": None,
        "path_hints": {},
        "error": None,
    }

    # 优先用 robots.txt 声明的 sitemap URL，否则回退到默认路径
    candidates = list(robots_sitemaps) if robots_sitemaps else []
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    candidates.extend([f"{root}/sitemap.xml", f"{root}/sitemap_index.xml"])

    urls = []
    used_url = None
    for sm_url in candidates:
        try:
            r = get(sm_url, timeout=timeout)
            if r.status_code == 200 and r.text.strip():
                urls = _parse_sitemap_urls(sm_url, timeout=timeout)
                if urls:
                    used_url = sm_url
                    break
        except Exception:
            continue

    if not urls:
        result["error"] = "sitemap 不可达或无内容"
        return result

    result["reachable"] = True
    result["used_url"] = used_url
    result["url_count"] = len(urls)

    # 最新 lastmod
    lastmods = [u["lastmod"] for u in urls if u.get("lastmod")]
    if lastmods:
        result["latest_lastmod"] = max(lastmods)

    # 路径分布（取每 URL 的第一级路径段）
    from collections import Counter
    paths = Counter()
    for u in urls:
        p = urlparse(u["url"]).path.strip("/").split("/")
        if p and p[0]:
            paths[p[0]] += 1
    result["path_hints"] = dict(paths.most_common(10))

    return result


def fetch_robots(url: str, timeout: int = 15) -> dict:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    result = {
        "url": robots_url, "exists": False, "content": "",
        "ai_crawler_status": {}, "sitemaps": [], "errors": [],
    }
    try:
        resp = get(robots_url, timeout=timeout)
        if resp.status_code != 200:
            result["errors"].append(f"robots.txt 返回 {resp.status_code}")
            for c in AI_CRAWLERS:
                result["ai_crawler_status"][c] = "NO_ROBOTS_TXT" if resp.status_code == 404 else "UNKNOWN"
            return result

        result["exists"] = True
        result["content"] = resp.text

        agent_rules = {}
        current = None
        for line in resp.text.split("\n"):
            line = line.strip()
            low = line.lower()
            if low.startswith("user-agent:"):
                current = line.split(":", 1)[1].strip()
                agent_rules.setdefault(current, [])
            elif low.startswith("disallow:") and current:
                agent_rules[current].append(("Disallow", line.split(":", 1)[1].strip()))
            elif low.startswith("allow:") and current:
                agent_rules[current].append(("Allow", line.split(":", 1)[1].strip()))
            elif low.startswith("sitemap:"):
                sm = line.split(":", 1)[1].strip()
                result["sitemaps"].append(sm if sm.startswith("http") else "http" + sm)

        for crawler in AI_CRAWLERS:
            if crawler in agent_rules:
                rules = agent_rules[crawler]
                if any(d == "Disallow" and p == "/" for d, p in rules):
                    result["ai_crawler_status"][crawler] = "BLOCKED"
                elif any(d == "Disallow" and p for d, p in rules):
                    result["ai_crawler_status"][crawler] = "PARTIALLY_BLOCKED"
                else:
                    result["ai_crawler_status"][crawler] = "ALLOWED"
            elif "*" in agent_rules:
                wild = agent_rules["*"]
                if any(d == "Disallow" and p == "/" for d, p in wild):
                    result["ai_crawler_status"][crawler] = "BLOCKED_BY_WILDCARD"
                else:
                    result["ai_crawler_status"][crawler] = "ALLOWED_BY_DEFAULT"
            else:
                result["ai_crawler_status"][crawler] = "NOT_MENTIONED"

        # sitemap 可达性 + 内容分析
        result["sitemap"] = _analyze_sitemap(url, result["sitemaps"], timeout=timeout)
    except Exception as e:
        result["errors"].append(f"读取 robots.txt 失败：{e}")
        result["sitemap"] = {"declared_in_robots": False, "reachable": False, "error": str(e)}
    return result
