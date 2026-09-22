"""作者信号：从所有可能位置提取作者名。

采集位置：
- Article/Person schema 的 author
- <meta name="author">
- HTML 正文中的「by X」「By X」「作者：X」等 byline
- HTML 中「About the Author」段落提及的人名
- 页面 <time> 标签旁的署名

本模块只采集事实，不做「是否同一人」的判断——交给 consistency 分析器。
"""
from __future__ import annotations

import re

from . import make_signal


# 常见 byline 模式（中英文都覆盖）
BYLINE_PATTERNS = [
    re.compile(r"\bby\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,2})\b", re.I),
    re.compile(r"作者[：:]\s*([^\s,，。.;；]{2,30})"),
    re.compile(r"文[/／]\s*([^\s,，。.;；]{2,30})"),
    re.compile(r"written\s+by\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,2})\b", re.I),
]

# About the Author 段落的开头
ABOUT_AUTHOR_RE = re.compile(
    r"About\s+the\s+Author[：:\s]*(.{0,500})",
    re.I | re.S,
)
# 从 About the Author 段落中提取人名（英文为主）
NAME_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b")


def _extract_names_from_schema(structured_data) -> list:
    """从 JSON-LD 中提取所有 Person.name 与 Article.author.name。"""
    hits = []

    def walk(node, path=""):
        if isinstance(node, dict):
            t = node.get("@type")
            if t == "Person" and node.get("name"):
                hits.append((node["name"], f"{path}.Person.name"))
            if t in ("Article", "BlogPosting", "NewsArticle"):
                author = node.get("author")
                if isinstance(author, dict):
                    name = author.get("name")
                    if name:
                        hits.append((name, f"{path}.Article.author.name"))
                elif isinstance(author, list):
                    for i, a in enumerate(author):
                        if isinstance(a, dict) and a.get("name"):
                            hits.append((a["name"], f"{path}.Article.author[{i}].name"))
                        elif isinstance(a, str):
                            hits.append((a, f"{path}.Article.author[{i}]"))
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")

    for sd in structured_data or []:
        walk(sd, "schema")
    return hits


def _extract_byline_from_text(text: str) -> list:
    """从正文文本中提取 byline（如 'by Betty Yuan'）。

    注意：抓取结果常是「Betty Yuan TL」「Betty Yuan Which」——名字后接的
    TL;DR / 标题首词会被误合并。这里做严格清理：
    - 只保留前 2-3 个词（西方人名通常 2 词）
    - 第 3 个词若是常见英文大写词（TL/Which/The/How/What）则截断
    """
    hits = []
    for pat in BYLINE_PATTERNS:
        for m in pat.finditer(text[:3000]):  # byline 通常在开头
            name = m.group(1).strip()
            # 清理：按空格分词，保留前 2 个词（若第 3 个词明显不是人名一部分则截断）
            words = name.split()
            if len(words) > 2:
                # 第 3 个词若是常见非人名词，截断
                stop_words = {"TL", "TL;DR", "TLDR", "Which", "What", "How", "Why",
                              "The", "A", "An", "In", "On", "At", "For", "To",
                              "Is", "Are", "This", "That", "These", "Those",
                              "Best", "Top", "New", "More"}
                if words[2] in stop_words or words[2].isupper():
                    name = " ".join(words[:2])
                else:
                    name = " ".join(words[:3])
            # 过滤常见误匹配
            if name.lower() in ("the", "and", "for", "with", "a", "an"):
                continue
            if len(name) < 2 or len(name) > 40:
                continue
            hits.append((name, f"HTML byline (pos {m.start()})"))
    return hits


def _extract_about_author(text: str) -> list:
    """从 About the Author 段落提取人名。"""
    hits = []
    m = ABOUT_AUTHOR_RE.search(text)
    if m:
        bio = m.group(1)[:500]
        # 段落里的第一个大写人名通常是作者
        for nm in NAME_RE.finditer(bio):
            name = nm.group(1)
            # 过滤常见非人名
            skip = {"About the", "He Is", "She Is", "The Author"}
            if name in skip:
                continue
            hits.append((name, "HTML .about-author"))
            break  # 只取第一个
    return hits


def gather(page: dict) -> dict:
    """聚合所有作者信号。"""
    if "error" in page:
        return {}

    url = page.get("url", "")
    meta = page.get("meta_tags", {}) or {}
    text = page.get("text_content", "") or ""
    sd = page.get("structured_data", []) or []

    schema_hits = _extract_names_from_schema(sd)
    byline_hits = _extract_byline_from_text(text)
    bio_hits = _extract_about_author(text)
    meta_author = meta.get("author") or ""

    return {
        "url": make_signal(url, "page.url"),
        "schema_authors": make_signal(
            [h[0] for h in schema_hits],
            "schema:Article.author / Person.name",
            raw=str(schema_hits),
        ),
        "schema_author_locations": make_signal(
            [h[1] for h in schema_hits], "schema locations"),
        "byline_authors": make_signal(
            [h[0] for h in byline_hits], "HTML byline", raw=str(byline_hits[:5])),
        "bio_authors": make_signal(
            [h[0] for h in bio_hits], "HTML .about-author", raw=str(bio_hits[:5])),
        "meta_author": make_signal(meta_author, 'HTML <meta name="author">'),
        "all_names": make_signal(
            sorted({h[0] for h in schema_hits + byline_hits + bio_hits} |
                   ({meta_author} if meta_author else set())),
            "aggregated"),
    }
