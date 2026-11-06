"""Markdown 渲染器 — ReportData → 结构化 Markdown（供 API/平台接入/快速预览）。

与 HTML 报告分工：HTML 是客户交付物（图表+排版），
Markdown 是数据摘要（分数 + findings + 行动清单），不做视觉设计。
"""
from __future__ import annotations

from pathlib import Path


def render_markdown(report, output_path=None) -> str:
    lines = []
    a = lines.append

    a(f"# {report.domain} GEO 诊断报告")
    a("")
    a(f"- 诊断对象：{report.url}")
    a(f"- 诊断日期：{report.audit_date}")
    a(f"- GEO 总分：**{report.geo_score}**（{report.label_zh or report.label}）")
    if report.description:
        a(f"- 核心判断：{report.description}")
    # P0 前置修改判定：与 HTML 报告的「核心判断 / 行动清单」两处提示保持一致。
    p0_note = getattr(report.executive_summary, "p0_blocking_note", "") if report.executive_summary else ""
    if p0_note:
        a(f"- P0 前置修改判定：{p0_note}")
    a("")

    a("## 维度评分")
    a("")
    a("| 维度 | 得分 | 权重 | 说明 |")
    a("|---|---|---|---|")
    for c in report.components:
        note = (c.note or "").replace("|", "\\|")
        a(f"| {c.name} | {c.score if c.measured else '未测量'} | {c.weight:.0%} | {note} |")
    a("")

    if report.findings:
        a("## 发现（Findings）")
        a("")
        for i, f in enumerate(report.findings, 1):
            sev = f.severity_zh or f.severity
            a(f"### {i}. {f.title}")
            a("")
            a(f"- 严重度：{sev}｜置信度：{f.confidence_zh or f.confidence}｜层级：{f.layer_zh or f.layer or '—'}")
            if f.evidence:
                a(f"- 证据：{f.evidence}")
            if f.impact:
                a(f"- 影响：{f.impact}")
            if f.fix:
                a(f"- 修复：{f.fix}")
            a("")

    if report.priority_actions:
        a("## 优先级行动")
        a("")
        p0_note = getattr(report.executive_summary, "p0_blocking_note", "") if report.executive_summary else ""
        if p0_note:
            a(f"> **P0 前置修改判定**：{p0_note}")
            a("")
        for pa in report.priority_actions:
            title = pa.title or (pa.action or "")[:40]
            a(f"- [ ] **{pa.priority}** {title}"
              f"（影响：{pa.impact_zh or pa.impact}｜工作量：{pa.effort_zh or pa.effort}｜负责：{pa.owner_zh or pa.owner}）")
        a("")

    if report.roadmap:
        a("## Roadmap")
        a("")
        for ph in report.roadmap:
            a(f"### {ph.phase} — {ph.title}")
            a("")
            for act in ph.actions:
                # actions 是 (finding_index, 短描述) 二元组
                text = act[1] if isinstance(act, (list, tuple)) and len(act) == 2 else str(act)
                a(f"- {text}")
            a("")

    md = "\n".join(lines)
    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
    return md
