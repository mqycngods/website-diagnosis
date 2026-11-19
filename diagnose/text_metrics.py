"""正文长度度量：中英文混合口径的统一实现。

中文没有空格分词，直接用 `text.split()` 会把整段当成 1 个"词"，导致
薄内容判定（diagnose/blockers.py）、论证分析阈值（analyzers/argumentation.py）
与功能页判定（analyzers/coverage.py）对中文站静默失效。
fetch / citability / analyzers 必须共用这里的一份实现，避免两套口径并存
（2026-11 统一）。
"""
from __future__ import annotations

import re

_CJK = re.compile(r"[一-鿿]")


def word_count(text: str) -> int:
    """中英文混合的"词数"估计：英文按空格分词，中文按字符计。"""
    if not text:
        return 0
    latin_words = len([w for w in text.split() if not _CJK.search(w)])
    cjk_chars = len(_CJK.findall(text))
    return latin_words + cjk_chars
