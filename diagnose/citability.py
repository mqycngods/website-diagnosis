"""段落可引用性评分（确定性简化版）— 五维度加权，中英通用正则。

五维度（满分）：
  答案块质量(30) 自足性(25) 结构可读性(20) 统计密度(15) 独特信号(10)
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .config import cfg
from .http_utils import get

DIM_MAX = cfg("citability.dim_max", {"answer_block_quality": 30, "self_containment": 25,
                                     "structural_readability": 20, "statistical_density": 15,
                                     "uniqueness_signals": 10})

_CJK = re.compile(r"[一-鿿]")


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

    soup = BeautifulSoup(resp.text, "lxml")
    for el in soup.find_all(["script", "style", "nav", "footer", "header", "aside", "form"]):
        el.decompose()

    blocks, cur_h, cur_p = [], "引言", []
    for el in soup.find_all(["h1", "h2", "h3", "h4", "p", "ul", "ol", "table"]):
        if el.name.startswith("h"):
            if cur_p:
                combined = " ".join(cur_p)
                if _wc(combined) >= 20:
                    blocks.append((cur_h, combined))
            cur_h, cur_p = el.get_text(strip=True), []
        else:
            t = el.get_text(strip=True)
            if t and _wc(t) >= 5:
                cur_p.append(t)
    if cur_p:
        combined = " ".join(cur_p)
        if _wc(combined) >= 20:
            blocks.append((cur_h, combined))

    scored = [_score_passage(content, heading) for heading, content in blocks]
    if not scored:
        result["errors"].append("未找到 20 词以上的内容段落")
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
