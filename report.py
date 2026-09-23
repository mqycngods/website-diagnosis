#!/usr/bin/env python3
"""
site-diagnosis-zh — 唯一 CLI 入口。

子命令：
  collect <url> --out <dir>     确定性采集，产出 work/*.json
  render <dir> --domain D --url U --output F.html   渲染中文 HTML 报告

Phase 2（agent 评分与中文撰写）在 agent 侧完成，不经此脚本——见 SKILL.md。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from diagnose import bots as bots_mod
from diagnose import citability as cit_mod
from diagnose import fetch as fetch_mod
from diagnose import robots as robots_mod
from diagnose import schema_check as schema_mod


def _write(out_dir: Path, name: str, data: dict):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / name).write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"  ✓ {name}")


def _err(msg: str) -> dict:
    return {"error": msg}


def _build_schema(page: dict, articles: dict) -> dict:
    """从 fetch.json + articles.json 的 structured_data 构建 schema.json 数据。
    collect 与 fetch-sampled 共用，保证两阶段产出一致。
    同时透传各页 fetch_errors，让 schema_check 能识别 Broken / 字段级质量问题。"""
    sd = list(page.get("structured_data", [])) if "error" not in page else []
    fetch_errors = list(page.get("errors", [])) if "error" not in page else []
    for ap in articles.get("pages", []):
        sd.extend(ap.get("structured_data", []) or [])
        fetch_errors.extend(ap.get("errors", []) or [])
    for pp in articles.get("product_pages", []):
        sd.extend(pp.get("structured_data", []) or [])
        fetch_errors.extend(pp.get("errors", []) or [])
    sampled = {
        "homepage": 1 if "error" not in page else 0,
        "article_pages": sum(1 for ap in articles.get("pages", []) if "error" not in ap),
        "product_pages": sum(1 for pp in articles.get("product_pages", []) if "error" not in pp),
    }
    is_ecom = schema_mod.detect_ecommerce(
        page.get("internal_links", []) if "error" not in page else [])
    schema = schema_mod.check_schema(
        sd, sampled=sampled, is_ecommerce=is_ecom, fetch_errors=fetch_errors)
    schema["sampled_pages"] = sum(sampled.values())
    return schema


def _fetch_urls_into(articles: dict, article_urls: list, product_urls: list):
    """逐页抓取并填入 articles dict；失败页写 error 不中断。"""
    from diagnose.config import cfg
    delay = float(cfg("crawler.polite_delay", 1))
    articles["sampled"] = article_urls
    articles["sampled_products"] = product_urls
    for u in article_urls:
        try:
            articles["pages"].append(fetch_mod.fetch_page(u))
        except Exception as e:
            err = _err(f"抓取失败：{e}")
            err["url"] = u
            articles["pages"].append(err)
        time.sleep(delay)  # 礼貌限速
    for u in product_urls:
        try:
            articles["product_pages"].append(fetch_mod.fetch_page(u))
        except Exception as e:
            err = _err(f"抓取失败：{e}")
            err["url"] = u
            articles["product_pages"].append(err)
        time.sleep(delay)


def _build_analysis(page: dict, articles: dict, pages_audit: dict = None) -> tuple:
    """对主页 + 文章页 + 产品页 + 功能页跑信号采集与全部分析器。

    返回 (signals, issues_by_analyzer)。失败容错，不阻断主流程。
    """
    try:
        from diagnose.signals import gatherer
        found_pages = (pages_audit or {}).get("found", {}) if isinstance(pages_audit, dict) else {}
        signals = gatherer.gather_all(page, articles, functional_pages=found_pages)
    except Exception as e:
        signals = {"error": f"signals 采集失败：{e}"}

    try:
        from diagnose import analyzers
        issues = analyzers.run_all(signals)
    except Exception as e:
        issues = {"_error": [{"kind": "pipeline_error", "error": str(e)}]}

    return signals, issues


def cmd_collect(url: str, out_dir: Path, cache_dir: Path = None):
    print(f"[collect] {url} -> {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 装配 HTTP 缓存：默认进程内记忆缓存（fetch/bots 基线/citability/sitemap 不重复请求）；
    # --cache-dir 开启文件系统缓存（调试重跑不重新抓站）。
    from diagnose import http_utils
    from diagnose.cache import FileCache, MemoryCache
    http_utils.set_cache(FileCache(cache_dir) if cache_dir else MemoryCache())

    # 1. fetch（后续 schema 复用其 structured_data）
    try:
        page = fetch_mod.fetch_page(url)
    except Exception as e:
        page = _err(f"fetch 失败：{e}")
    _write(out_dir, "fetch.json", page)

    # 1b. 文章页 + 产品页抽样（新鲜度与 Article/Product schema 是页面级信号，首页通常不带）
    #     正则采样为空时打 pending 标记 + 暴露 internal_links，交给 agent 人工挑选后
    #     用 `report.py fetch-sampled` 补齐（见 SKILL.md Phase 1.5）。
    try:
        from diagnose import sampling
        from diagnose.config import cfg
        links = page.get("internal_links", []) if "error" not in page else []
        article_urls = sampling.pick_article_urls(links, limit=cfg("crawler.sample_articles", 5))
        product_urls = sampling.pick_product_urls(links, limit=cfg("crawler.sample_products", 3))
        articles = {"sampled": article_urls, "sampled_products": product_urls, "pages": [],
                    "product_pages": []}
        if not article_urls and not product_urls:
            articles["pending_agent_pick"] = True
            articles["internal_links"] = links
        else:
            _fetch_urls_into(articles, article_urls, product_urls)
    except Exception as e:
        articles = _err(f"页面采样失败：{e}")
    _write(out_dir, "articles.json", articles)

    # 1c. 功能页发现（Support/FAQ/Contact/About/Warranty）——基于首页链接 + sitemap，
    #     只抓真实发现的 URL；未发现的类型列入 missing，绝不拼路径猜测。
    try:
        from diagnose import discovery
        pages_audit = discovery.audit_functional_pages(
            url,
            page.get("internal_links", []) if "error" not in page else [],
            fetch_one=fetch_mod.fetch_page)
    except Exception as e:
        pages_audit = _err(f"功能页发现失败：{e}")
    _write(out_dir, "pages.json", pages_audit)

    # 2. schema（首页 + 抽样文章页 + 抽样产品页的 structured_data 合并，避免重复抓取）
    try:
        schema = _build_schema(page, articles)
    except Exception as e:
        schema = _err(f"schema 检测失败：{e}")
    _write(out_dir, "schema.json", schema)

    # 3. robots.txt 声明
    try:
        robots = robots_mod.fetch_robots(url)
    except Exception as e:
        robots = _err(f"robots 解析失败：{e}")
    _write(out_dir, "robots.json", robots)

    # 4. bots UA 实测
    try:
        bots = bots_mod.probe_bots(url)
    except Exception as e:
        bots = _err(f"爬虫实测失败：{e}")
    _write(out_dir, "bots.json", bots)

    # 5. citability
    try:
        cit = cit_mod.score_citability(url)
    except Exception as e:
        cit = _err(f"可引用性评分失败：{e}")
    _write(out_dir, "citability.json", cit)

    # 6. brand 站外实体（Wikipedia/Wikidata API 验证；其余平台待 agent 复核）
    try:
        from diagnose import brand as brand_mod
        brand = brand_mod.scan_brand(url=url)
    except Exception as e:
        brand = _err(f"品牌实体扫描失败：{e}")
    _write(out_dir, "brand.json", brand)

    # 7. technical 技术层（安全头矩阵 + HTTPS + llms.txt）
    try:
        from diagnose import technical as tech_mod
        tech = tech_mod.check_technical(
            url, security_headers=page.get("security_headers", {}) if "error" not in page else {})
    except Exception as e:
        tech = _err(f"技术层检测失败：{e}")
    _write(out_dir, "technical.json", tech)

    # 8. signals + analyzers（通用分析框架，功能页自 pages_audit 进入管线）
    try:
        signals, issues = _build_analysis(page, articles, pages_audit)
        _write(out_dir, "signals.json", signals)
        _write(out_dir, "issues.json", issues)
    except Exception as e:
        _write(out_dir, "signals.json", _err(f"signals 采集失败:{e}"))
        _write(out_dir, "issues.json", _err(f"issues 分析失败:{e}"))

    print("[collect] 完成")


def cmd_render(work_dir: Path, domain: str, url: str, output: Path, fmt: str = "html"):
    from render.build import build_report

    report = build_report(work_dir, domain=domain, url=url)
    if fmt == "json":
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report.to_dict(), ensure_ascii=False,
                                     indent=2, default=str), encoding="utf-8")
        print(f"[render] JSON 已生成：{output}")
    elif fmt == "markdown":
        from render.markdown import render_markdown
        render_markdown(report, output)
        print(f"[render] Markdown 已生成：{output}")
    else:
        from render.html import render_html
        out = render_html(report, output)
        print(f"[render] 报告已生成：{out}")


def cmd_fetch_sampled(work_dir: Path):
    """读取 work/agent_picks.json（agent 从 internal_links 中人工挑选的 URL），
    抓取并回写 articles.json，再用最新数据重建 schema.json。

    agent_picks.json 格式：
      {"articles": ["url1", "url2", ...], "products": ["url3", ...]}
    两个键都可选，缺省视为空列表。
    """
    picks_file = work_dir / "agent_picks.json"
    if not picks_file.exists():
        print(f"[fetch-sampled] 未找到 {picks_file}；请先由 agent 写入该文件")
        return

    picks = json.loads(picks_file.read_text(encoding="utf-8"))
    article_urls = [u for u in picks.get("articles") or [] if isinstance(u, str)]
    product_urls = [u for u in picks.get("products") or [] if isinstance(u, str)]
    if not article_urls and not product_urls:
        print("[fetch-sampled] agent_picks.json 中 articles/products 均为空，无需抓取")
        return

    # 拉取已采集的 fetch.json 用于重建 schema
    fetch_file = work_dir / "fetch.json"
    page = json.loads(fetch_file.read_text(encoding="utf-8")) if fetch_file.exists() else {}

    articles = {"sampled": [], "sampled_products": [], "pages": [], "product_pages": []}
    print(f"[fetch-sampled] 抓取 {len(article_urls)} 篇文章页 + {len(product_urls)} 个产品页")
    _fetch_urls_into(articles, article_urls, product_urls)
    _write(work_dir, "articles.json", articles)

    try:
        schema = _build_schema(page, articles)
    except Exception as e:
        schema = _err(f"schema 重建失败：{e}")
    _write(work_dir, "schema.json", schema)

    # 补抓完后重建 signals + issues（功能页数据若已采集则一并进入）
    try:
        pages_file = work_dir / "pages.json"
        pages_audit = (json.loads(pages_file.read_text(encoding="utf-8"))
                       if pages_file.exists() else None)
        signals, issues = _build_analysis(page, articles, pages_audit)
        _write(work_dir, "signals.json", signals)
        _write(work_dir, "issues.json", issues)
    except Exception as e:
        print(f"[fetch-sampled] signals/issues 重建失败：{e}")
    print("[fetch-sampled] 完成")


def main() -> int:
    ap = argparse.ArgumentParser(description="中文官网 GEO 诊断报告生成器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="确定性采集")
    c.add_argument("url")
    c.add_argument("--out", required=True)
    c.add_argument("--cache-dir", default=None,
                   help="可选文件系统缓存目录（调试用：重跑不重新抓站）")

    f = sub.add_parser("fetch-sampled", help="读取 agent_picks.json 补抓页面并重建 schema")
    f.add_argument("work_dir")

    r = sub.add_parser("render", help="渲染报告（HTML / Markdown / JSON）")
    r.add_argument("work_dir")
    r.add_argument("--domain", required=True)
    r.add_argument("--url", required=True)
    r.add_argument("--output", required=True)
    r.add_argument("--format", choices=["html", "markdown", "json"], default="html")

    args = ap.parse_args()
    if args.cmd == "collect":
        cmd_collect(args.url, Path(args.out),
                    cache_dir=Path(args.cache_dir) if args.cache_dir else None)
    elif args.cmd == "fetch-sampled":
        cmd_fetch_sampled(Path(args.work_dir))
    elif args.cmd == "render":
        cmd_render(Path(args.work_dir), args.domain, args.url, Path(args.output),
                   fmt=args.format)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
