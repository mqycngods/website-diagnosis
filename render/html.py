"""Jinja2 渲染入口 — ReportData → 单文件中文 HTML。"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .charts import compute_charts

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def _autolink_urls(text: str) -> str:
    """把文本中的独立 URL 转换为 <a href="..." target="_blank"> 链接。

    严格匹配，清洗末尾附带的括号、句号等中英文标点，防止 URL 错乱。
    先做 HTML 转义，再提取 URL 替换为 <a>——防止 finding 文本里含
    <script>/<head>/<details> 等关键字时被浏览器当真实标签解析，破坏文档结构。
    """
    if not text or not isinstance(text, str):
        return text

    import html as _html
    # 先转义：< > & " ' 全部变为实体，URL 里的 : / ? & = # 不受影响（& 变 &amp; 仍可被正则命中）
    escaped = _html.escape(text, quote=False)

    url_pattern = re.compile(r'(https?://[^\s<>"\'()（）「」『』、，。；！？]+)')

    def replace_match(m):
        raw_url = m.group(1)
        # 清除结尾处因括号或标点多抓的字符
        clean_url = re.sub(r'[\)）\>、，。；！？\.,;!]+$', '', raw_url)
        trailing = raw_url[len(clean_url):]
        # href 属性里的 & 要还原为 &amp;（_html.escape 已把 & 转为 &amp;），href 属性值保持转义态合法
        return f'<a href="{clean_url}" target="_blank" rel="noopener noreferrer" style="color:var(--accent,#1a73e8);text-decoration:underline;text-underline-offset:2px;word-break:break-all">{clean_url}</a>{trailing}'

    return url_pattern.sub(replace_match, escaped)


def render_html(report, output_path, offline: bool = False) -> Path:
    report.chart_data = compute_charts(report)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(("html", "j2")),
        trim_blocks=True, lstrip_blocks=True,
    )
    # 注册 autolink 过滤器
    env.filters["autolink"] = _autolink_urls

    def _safe(o):
        return json.dumps(o, ensure_ascii=False, default=str).replace("</", "<\\/")

    echarts_js = ""
    if offline:
        ejs = Path(__file__).resolve().parent.parent / "assets" / "echarts.min.js"
        if ejs.exists():
            echarts_js = ejs.read_text(encoding="utf-8")

    template = env.get_template("base.html.j2")
    html = template.render(
        report=report,
        report_json=_safe(report.to_dict()),
        chart_json=_safe(report.chart_data),
        offline=offline, echarts_js=echarts_js,
        generated_at=datetime.now().isoformat(timespec="seconds"),
    )

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
