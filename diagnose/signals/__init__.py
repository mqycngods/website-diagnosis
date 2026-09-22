"""Signals — 页面信号采集层。

职责：从已抓取的页面（fetch_page 结果）提取**纯事实**，不做任何判断。
每个采集器是纯函数：gather(page: dict) -> dict

输出契约：每个信号都是 {"value": ..., "location": "...", "raw": "..."} 结构，
便于分析器引用与定位。
"""
from __future__ import annotations

# 统一信号记录结构
def make_signal(value, location: str, raw: str = "") -> dict:
    """所有信号的统一格式：值 + 出现位置 + 原始片段。

    - value: 提取出的值（字符串/数字/字典）
    - location: 在页面/Schema 中的位置描述（如 "Article.author.name" / "HTML .byline"）
    - raw: 原始片段（供调试与报告展示）
    """
    return {"value": value, "location": location, "raw": raw[:500] if isinstance(raw, str) else raw}
