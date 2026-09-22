"""抓取页面并提取结构化信息：title/meta/标题/正文/JSON-LD/响应头/SSR 判定。"""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .http_utils import get, is_challenge_page

SECURITY_HEADERS = [
    "Strict-Transport-Security", "Content-Security-Policy", "X-Frame-Options",
    "X-Content-Type-Options", "Referrer-Policy", "Permissions-Policy",
]


def fetch_page(url: str, timeout: int = 30) -> dict:
    result = {
        "url": url, "status_code": None, "title": None, "description": None,
        "canonical": None, "meta_tags": {}, "h1_tags": [], "heading_structure": [],
        "word_count": 0, "text_content": "", "internal_links": [], "external_links": [],
        "images": [], "structured_data": [], "security_headers": {},
        "freshness": {}, "has_ssr_content": True, "challenge_detected": False, "errors": [],
    }
    if urlparse(url).scheme not in ("http", "https"):
        result["errors"].append(f"不支持的协议：{url}")
        return result

    try:
        resp = get(url, timeout=timeout)
        if is_challenge_page(resp.text, resp.status_code):
            result["challenge_detected"] = True
        result["status_code"] = resp.status_code
        result["security_headers"] = {h: resp.headers.get(h) for h in SECURITY_HEADERS}

        soup = BeautifulSoup(resp.text, "lxml")

        title = soup.find("title")
        result["title"] = title.get_text(strip=True) if title else None

        for meta in soup.find_all("meta"):
            name = meta.get("name", meta.get("property", ""))
            content = meta.get("content", "")
            if name and content:
                result["meta_tags"][name.lower()] = content
                if name.lower() == "description":
                    result["description"] = content

        canonical = soup.find("link", rel="canonical")
        result["canonical"] = canonical.get("href") if canonical else None

        for level in range(1, 7):
            for h in soup.find_all(f"h{level}"):
                text = h.get_text(strip=True)
                result["heading_structure"].append({"level": level, "text": text})
                if level == 1:
                    result["h1_tags"].append(text)

        # JSON-LD 需在 decompose 前提取
        for script in soup.find_all("script", type="application/ld+json"):
            raw = script.string or script.get_text() or ""
            raw_stripped = raw.strip()
            if not raw_stripped:
                continue

            # 尝试从原始文本猜测 @type（即便 JSON 不合法，也保留类型线索）
            type_guess_m = re.search(r'"@type"\s*:\s*"([^"]+)"', raw_stripped)
            guessed_type = type_guess_m.group(1) if type_guess_m else "Unknown"

            # 第 1 步：尝试直接解析
            try:
                data = json.loads(raw_stripped)
                _validate_jsonld_content(data, result, url)
                result["structured_data"].append(data)
                continue
            except (json.JSONDecodeError, TypeError) as e1:
                first_err = e1

            # 第 2 步：尝试修复 HTML 实体编码后重试
            try:
                fixed = (raw_stripped
                         .replace("&lt;", "<").replace("&gt;", ">")
                         .replace("&quot;", '"').replace("&amp;", "&")
                         .replace("&#39;", "'").replace("&apos;", "'"))
                data = json.loads(fixed)
                result["errors"].append({
                    "kind": "jsonld_html_entities",
                    "schema_type": guessed_type,
                    "auto_fixed": True,
                    "page_url": url,
                    "msg": f"{guessed_type} JSON-LD 含 HTML 实体编码，已自动修复后解析",
                    "raw_preview": raw_stripped[:300],
                })
                _validate_jsonld_content(data, result, url)
                result["structured_data"].append(data)
                continue
            except (json.JSONDecodeError, TypeError):
                pass

            # 第 3 步：尝试修复字符串内未转义换行符
            try:
                def _escape_str_newlines(m):
                    s = m.group(0)
                    return s.replace("\r", "\\r").replace("\n", "\\n")
                fixed = re.sub(r'"(?:[^"\\]|\\.)*"', _escape_str_newlines,
                               raw_stripped, flags=re.S)
                data = json.loads(fixed)
                result["errors"].append({
                    "kind": "jsonld_unescaped_newline",
                    "schema_type": guessed_type,
                    "auto_fixed": True,
                    "page_url": url,
                    "msg": f"{guessed_type} JSON-LD 字符串内含未转义换行符，已自动修复",
                    "raw_preview": raw_stripped[:300],
                })
                _validate_jsonld_content(data, result, url)
                result["structured_data"].append(data)
                continue
            except (json.JSONDecodeError, TypeError):
                pass

            # 第 4 步：尝试清除 JSON-LD 中非法的 JS 注释（// 与 /* */）
            try:
                text_no_multiline = re.sub(r'/\*.*?\*/', '', raw_stripped, flags=re.DOTALL)
                lines = []
                for line in text_no_multiline.splitlines():
                    stripped_line = line.strip()
                    if stripped_line.startswith('//'):
                        continue
                    line = re.sub(r'\s+//.*$', '', line)
                    lines.append(line)
                cleaned_comments = '\n'.join(lines)
                data = json.loads(cleaned_comments)
                result["errors"].append({
                    "kind": "jsonld_comments",
                    "schema_type": guessed_type,
                    "auto_fixed": True,
                    "page_url": url,
                    "msg": f"{guessed_type} JSON-LD 内嵌非法 JavaScript 注释（//），已自动清洗注释后成功解析",
                    "raw_preview": raw_stripped[:300],
                })
                _validate_jsonld_content(data, result, url)
                result["structured_data"].append(data)
                continue
            except (json.JSONDecodeError, TypeError):
                pass

            # 真的修不好——塞占位让 schema_check 知道「存在但损坏」
            # 记录 block 索引以便下游明确受影响的 Block 位置
            script_idx = len(result["structured_data"])
            result["errors"].append({
                "kind": "invalid_jsonld",
                "schema_type": guessed_type,
                "auto_fixed": False,
                "page_url": url,
                "script_index": script_idx,
                "error": str(first_err),
                "raw_preview": raw_stripped[:500],
                "raw_length": len(raw_stripped),
            })
            result["structured_data"].append({
                "@type": guessed_type,
                "__parse_error": True,
                "__error": str(first_err),
                "__page_url": url,
                "__script_index": script_idx,
                "__raw_preview": raw_stripped[:500],
            })

        # 内容新鲜度（依赖 structured_data + soup + 响应头）
        from .freshness import extract_freshness
        result["freshness"] = extract_freshness(
            result["structured_data"], soup, dict(resp.headers))

        # SSR 判定：框架根节点 + 正文词数（在 decompose 前测）
        js_roots = soup.find_all(id=re.compile(r"(app|root|__next|__nuxt)", re.I))
        root_text_len = [len(r.get_text(strip=True)) for r in js_roots]

        for el in soup.find_all(["script", "style", "nav", "footer", "header"]):
            el.decompose()
        text = soup.get_text(separator=" ", strip=True)
        result["text_content"] = text
        result["word_count"] = len(text.split())

        base = urlparse(url).netloc
        for a in soup.find_all("a", href=True):
            href = urljoin(url, a["href"])
            if urlparse(href).netloc == base:
                result["internal_links"].append({"url": href, "text": a.get_text(strip=True)})
            elif urlparse(href).scheme in ("http", "https"):
                result["external_links"].append({"url": href, "text": a.get_text(strip=True)})

        for img in soup.find_all("img"):
            result["images"].append({
                "src": img.get("src", ""), "alt": img.get("alt", ""),
                "width": img.get("width"), "height": img.get("height"),
            })

        if js_roots:
            for i, tl in enumerate(root_text_len):
                if tl < 50 and result["word_count"] < 200:
                    result["has_ssr_content"] = False
                    result["errors"].append("疑似纯客户端渲染：无服务端正文")

    except Exception as e:  # 网络/解析异常统一收容
        result["errors"].append(f"抓取失败：{e}")
    return result


def _validate_jsonld_content(data, result, page_url=None):
    """JSON-LD 内容级校验：即便语法合法，也检查字段质量。

    发现的问题写入 result["errors"]，但不阻断数据流。
    """
    if not isinstance(data, dict):
        return
    schema_type = data.get("@type", "Unknown")

    # description 字段质量
    desc = data.get("description")
    if isinstance(desc, str):
        if re.search(r"&(lt|gt|quot|amp|nbsp|#39|apos);", desc):
            result["errors"].append({
                "kind": "jsonld_html_entities_in_field",
                "schema_type": schema_type,
                "field": "description",
                "page_url": page_url,
                "msg": f"{schema_type}.description 含 HTML 实体编码（如 &lt; &quot;），应为纯文本",
                "sample": desc[:200],
            })
        if re.search(r"<(div|p|h\d|ul|li|span|br|table)\b", desc, re.I):
            result["errors"].append({
                "kind": "jsonld_html_tags_in_field",
                "schema_type": schema_type,
                "field": "description",
                "page_url": page_url,
                "msg": f"{schema_type}.description 含 HTML 标签，应为纯文本",
                "sample": desc[:200],
            })
        if len(desc) > 5000:
            result["errors"].append({
                "kind": "jsonld_description_too_long",
                "schema_type": schema_type,
                "field": "description",
                "page_url": page_url,
                "msg": f"{schema_type}.description 长度 {len(desc)} 字符，建议 ≤300",
            })

    # Product 类型关键字段检查
    if schema_type == "Product":
        brand = data.get("brand")
        if isinstance(brand, dict) and "@type" not in brand:
            result["errors"].append({
                "kind": "jsonld_missing_field",
                "schema_type": "Product",
                "field": "brand.@type",
                "page_url": page_url,
                "msg": 'Product.brand 缺 @type（应为 "Brand"）',
            })
        if data.get("aggregateRating") and not data.get("review"):
            result["errors"].append({
                "kind": "jsonld_missing_field",
                "schema_type": "Product",
                "field": "review",
                "page_url": page_url,
                "msg": "Product 有 aggregateRating 但无 review 数组，建议补 1-3 条具体用户评价",
            })


def extract_sameas(structured_data) -> list:
    """从 JSON-LD 提取所有 sameAs 链接（去重）。"""
    links = []

    def walk(node):
        if isinstance(node, dict):
            sa = node.get("sameAs")
            if isinstance(sa, str):
                links.append(sa)
            elif isinstance(sa, list):
                links.extend(s for s in sa if isinstance(s, str))
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(structured_data or [])
    seen, out = set(), []
    for l in links:
        if l not in seen:
            seen.add(l)
            out.append(l)
    return out
