"""段落可引用性评分（确定性简化版）— 五维度加权，中英通用正则。

五维度（满分）：
  答案块质量(30) 自足性(25) 结构可读性(20) 统计密度(15) 独特信号(10)
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup, Comment, NavigableString

from .config import cfg
from .http_utils import get, is_challenge_page

DIM_MAX = cfg("citability.dim_max", {"answer_block_quality": 30, "self_containment": 25,
                                     "structural_readability": 20, "statistical_density": 15,
                                     "uniqueness_signals": 10})

_CJK = re.compile(r"[一-鿿]")

# 可引用块的最小长度（词）：低于此值的通常是导航项、按钮文案、小标题碎片，
# 它们既不能被独立引用，积累多了还会稀释整页均分。
MIN_BLOCK_WORDS = 20

# 块级容器：决定「这一段文本归谁」。不限 p，div/section/li/td 等堆叠正文同样纳入，
# 否则把正文全部塞进 div 的 SPA / Nuxt 站点会被误判成 0 个可引用段落。
_BLOCK_TAGS = [
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "div", "section", "article", "main", "aside", "ul", "ol", "li",
    "dl", "dt", "dd", "table", "thead", "tbody", "tr", "td", "th",
    "blockquote", "pre", "figure", "figcaption", "details", "summary",
]
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_BLOCK_SET = set(_BLOCK_TAGS)

# 纯 UI 噪音：既不是正文，也不该计入可引用性
_NOISE_TAGS = {"button", "select", "option", "label", "noscript", "svg", "template"}


def _is_visible(el) -> bool:
    """跳过 aria-hidden / display:none / hidden 的元素——AI 也看不见它们。"""
    style = (el.get("style") or "").replace(" ", "").lower()
    if "display:none" in style or "visibility:hidden" in style:
        return False
    if el.get("hidden") is not None:
        return False
    if (el.get("aria-hidden") or "").lower() == "true":
        return False
    return True


def _own_text(el) -> str:
    """只取挂在 el 名下、不属于任何块级后代的文本。

    每个文本节点只在「最深的块级祖先」处被取一次，因此全页拼起来不重不漏。
    内联子元素（span/a/strong/br…）整棵取，保证一句被 span 拆开的正文仍然完整。
    """
    parts = []
    for child in el.children:
        # Comment 也是 NavigableString 的子类，不排除就会把
        # Vue 的 <!--teleport start--> 之类框架注释当成正文
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif getattr(child, "name", None) and child.name not in _BLOCK_SET:
            # <br> 是行内换行而非块级分界，get_text 不会为它补分隔符；
            # 不显式补空格会把相邻两段焊成「...needs.But does its...」
            parts.append(" " + child.get_text(" ", strip=True) + " ")
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _is_link_soup(el) -> bool:
    """链接密度过高的块视为导航/列表入口，不是正文。

    很多站点不把导航放在 <nav> 里（Nuxt/Vue 常直接铺 div），只能按内容特征识别：
    一个块里绝大多数文字都是 <a> 的锚文本，说明它是入口列表而非叙述性段落。
    """
    text = el.get_text(" ", strip=True)
    if not text:
        return False
    link_text = " ".join(a.get_text(" ", strip=True) for a in el.find_all("a"))
    return len(link_text) > 0.6 * len(text)


def _split_long(text: str, limit: int = 400) -> list:
    """把超长容器（整块内嵌的文章全文）按句子切回段落级。

    用 div 承载正文的站点常把一个栏目的多篇内容全塞进同一个容器，切块器若整块
    送去评分，长度带会直接判 0 分，反而不如按句子还原成可引用的段落。
    """
    # 有站点把整篇文章渲染进单个文本节点、且句号后不补空格（"...needs.But does"），
    # 先按「标点紧跟大写字母/数字」补空格，这段文本才算能正常断句
    text = re.sub(r"(?<=[.!?])(?=[A-Z0-9])", " ", text)
    if _wc(text) <= limit:
        return [text]
    # 先按 <br> 转出的换行切，再按句末标点补切
    pieces = []
    for line in text.split("\n"):
        pieces.extend(re.split(r"(?<=[.!?。！？])\s+", line))
    out, cur = [], ""
    for s in pieces:
        s = s.strip()
        if not s:
            continue
        if cur and _wc(cur + " " + s) > limit // 2:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return [p for p in out if _wc(p) >= MIN_BLOCK_WORDS] or [text]


def _iter_blocks(soup):
    """按文档顺序产出 (kind, text)，kind 为 'heading' 或 'text'。

    不依赖 <p> 标签：正文写在 div/span 里同样能提取。
    """
    for el in soup.find_all(_BLOCK_TAGS):
        if el.name in _NOISE_TAGS or el.find_parent(_NOISE_TAGS):
            continue
        if not _is_visible(el):
            continue
        if el.name in _HEADING_TAGS:
            t = el.get_text(" ", strip=True)
            if t:
                yield "heading", t
            continue
        t = _own_text(el)
        if not t:
            continue
        # 入口列表（导航、链接墙）不是叙述性正文，不参与可引用性评分
        if len(t) > 60 and _is_link_soup(el) and _wc(t) < 120:
            continue
        yield "text", t


def _wc(text: str) -> int:
    """中英文混合的"词数"估计：英文按空格分词，中文按字符计。

    中文没有空格分词，直接用 .split() 会把整段当成 1 个"词"，导致长度带判定
    与统计逻辑全部失效。取 空格词数 + CJK 字符数 作为近似词数。
    """
    latin_words = len([w for w in text.split() if not _CJK.search(w)])
    cjk_chars = len(_CJK.findall(text))
    return latin_words + cjk_chars


def _score_passage(text: str, heading: str = "") -> dict:
    words = text.split()
    wc = _wc(text)
    s = {k: 0 for k in DIM_MAX}

    # 1. 答案块质量
    if re.search(r"\b\w+\s+(?:is|are|means|refers to)\s|是指|指的是|是一种|是一个", text, re.I):
        s["answer_block_quality"] += 15
    first60 = " ".join(words[:60])
    if re.search(r"\d+%|\$\d|\d+\s*(?:million|billion|万|亿)|是|为", first60):
        s["answer_block_quality"] += 15
    if heading and heading.rstrip().endswith(("?", "？")):
        s["answer_block_quality"] += 10
    sentences = re.split(r"[.!?。！？]+", text)
    clear = sum(1 for x in sentences if 4 <= len(x.split()) <= 25)
    if sentences:
        s["answer_block_quality"] += int(clear / len(sentences) * 10)
    if re.search(r"according to|studies? show|research shows|数据显示|研究表明|根据", text, re.I):
        s["answer_block_quality"] += 10
    s["answer_block_quality"] = min(s["answer_block_quality"], 30)

    # 2. 自足性（长度带：英 134-167 / 中 90-120）
    if 90 <= wc <= 167:
        s["self_containment"] += 10
    elif 70 <= wc <= 200:
        s["self_containment"] += 7
    elif wc < 20 or wc > 400:
        s["self_containment"] += 0
    else:
        s["self_containment"] += 4
    pronouns = len(re.findall(r"\b(?:it|they|this|that|these|those|he|she)\b|它|他们|这|那", text, re.I))
    ratio = pronouns / wc if wc else 1
    s["self_containment"] += 8 if ratio < 0.02 else 5 if ratio < 0.04 else 3 if ratio < 0.06 else 0
    proper = len(re.findall(r"\b[A-Z][a-z]+\b", text))
    s["self_containment"] += 7 if proper >= 3 else 4 if proper >= 1 else 0
    s["self_containment"] = min(s["self_containment"], 25)

    # 3. 结构可读性
    if sentences:
        avg = wc / len(sentences)
        s["structural_readability"] += 8 if 8 <= avg <= 22 else 5 if 6 <= avg <= 26 else 2
    if re.search(r"first|second|finally|additionally|首先|其次|最后|此外|另外", text, re.I):
        s["structural_readability"] += 4
    if re.search(r"\d+[\.\)、]\s|步骤|第[一二三四]", text):
        s["structural_readability"] += 4
    if "\n" in text:
        s["structural_readability"] += 4
    s["structural_readability"] = min(s["structural_readability"], 20)

    # 4. 统计密度
    s["statistical_density"] += min(len(re.findall(r"\d+(?:\.\d+)?%", text)) * 3, 6)
    s["statistical_density"] += min(len(re.findall(r"\$[\d,]+|\d+\s*(?:元|万元|亿元)", text)) * 3, 5)
    s["statistical_density"] += min(len(re.findall(r"\b\d+(?:,\d{3})*\s*(?:users|customers|pages|个|家|人)", text, re.I)) * 2, 4)
    if re.search(r"\b20(?:1\d|2[0-6])\b", text):
        s["statistical_density"] += 2
    if re.search(r"Gartner|Forrester|McKinsey|Google|研究|报告|数据", text):
        s["statistical_density"] += 2
    s["statistical_density"] = min(s["statistical_density"], 15)

    # 5. 独特信号
    if re.search(r"our (?:research|study|data)|we found|我们的(?:研究|数据|调查)|我们发现", text, re.I):
        s["uniqueness_signals"] += 5
    if re.search(r"case study|for example|for instance|例如|比如|案例", text, re.I):
        s["uniqueness_signals"] += 3
    s["uniqueness_signals"] = min(s["uniqueness_signals"], 10)

    total = sum(s.values())
    grade = "A" if total >= 80 else "B" if total >= 65 else "C" if total >= 50 else "D" if total >= 35 else "F"
    return {"heading": heading, "content": text, "word_count": wc,
            "total_score": total, "grade": grade, "breakdown": s,
            "preview": " ".join(words[:30])}


def score_citability(url: str, timeout: int = 30) -> dict:
    result = {"url": url, "average_citability_score": 0.0, "total_blocks_analyzed": 0,
              "grade_distribution": {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0},
              "top_5_citable": [], "bottom_5_citable": [], "all_blocks": [], "errors": []}
    try:
        resp = get(url, timeout=timeout)
    except Exception as e:
        result["errors"].append(f"抓取失败：{e}")
        return result

    # 拿到的是 WAF 质询页时不能当作「页面没有正文」——否则会把限流误报成内容缺陷
    if is_challenge_page(resp.text, resp.status_code):
        result["errors"].append(
            f"抓到 WAF 质询页（HTTP {resp.status_code}），本次未测量，请重跑")
        return result

    soup = BeautifulSoup(resp.text, "lxml")
    for br in soup.find_all("br"):
        # <br> 是这个站点唯一的段落分界，保留它才能在长容器里正确切回段落
        br.replace_with(NavigableString("\n"))
    for el in soup.find_all(["script", "style", "nav", "footer", "header", "aside", "form",
                             "button", "select", "svg", "noscript"]):
        el.decompose()

    blocks, cur_h, cur_p = [], "引言", []
    for kind, text in _iter_blocks(soup):
        if kind == "heading":
            if cur_p:
                combined = " ".join(cur_p)
                if _wc(combined) >= MIN_BLOCK_WORDS:
                    blocks.append((cur_h, combined))
            cur_h, cur_p = text, []
        else:
            if _wc(text) >= 5:
                cur_p.append(text)
    if cur_p:
        combined = " ".join(cur_p)
        if _wc(combined) >= MIN_BLOCK_WORDS:
            blocks.append((cur_h, combined))

    # 超长块（整块内嵌的文章）按句子还原成段落，否则长度带直接判 0 分
    expanded = []
    for heading, content in blocks:
        for part in _split_long(content):
            expanded.append((heading, part))
    blocks = expanded

    scored = [_score_passage(content, heading) for heading, content in blocks]
    if not scored:
        result["errors"].append(f"未找到 {MIN_BLOCK_WORDS} 词以上的内容段落")
        return result

    for b in scored:
        result["grade_distribution"][b["grade"]] += 1
    result["all_blocks"] = scored
    result["total_blocks_analyzed"] = len(scored)
    result["average_citability_score"] = round(
        sum(b["total_score"] for b in scored) / len(scored), 1)
    by_score = sorted(scored, key=lambda x: x["total_score"], reverse=True)
    result["top_5_citable"] = by_score[:5]
    result["bottom_5_citable"] = by_score[-5:][::-1]
    return result
