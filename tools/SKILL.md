# 中文官网 GEO 诊断工具集 — Skill 描述

> 给 agent 平台用的技能说明。每个 HTTP 端点对应一个「工具/技能」，agent 按工作流调用。

**Base URL：** `http://0.0.0.0:8000`（本地）/ `https://your-domain.com`（线上）
**OpenAPI 导入：** `tools/openapi.json`（直接粘贴或 URL 导入）

---

## 工具 1：抓取网站事实 `site_collect`

- **Method / Path：** `POST /collect`
- **何时调用：** 用户给出一个 URL，要做 GEO 诊断的**第一步**。本工具一次性抓回该站的所有确定性事实（首页 HTML、robots、技术层、12 个 AI 爬虫 UA 实测、文章/产品页采样、功能页发现、schema 检测）。
- **入参：**
  ```json
  { "url": "https://example.com" }
  ```
- **返回：**
  ```json
  {
    "url": "...",
    "fetch":      { "title": "...", "internal_links": [...], "paragraphs": [...], "structured_data": [...], ... },
    "robots":     { "exists": true, "ai_crawlers": {...}, ... },
    "technical":  { "https": true, "security_headers": {...}, "core_web_vitals_hints": {...} },
    "bots":       { "results": [{"bot": "GPTBot", "status": 200, ...}, ...] },
    "citability": { "overall": 62, "dimensions": {...}, "paragraphs": [...] },
    "articles":   { "pages": [...], "product_pages": [...], "sampled": [...], "pending_agent_pick?": true, "internal_links?": [...] },
    "pages":      { "found": {"about": "...", "contact": "..."}, "missing": [...] },
    "schema":     { "coverage": {...}, "sampled_pages": 9 }
  }
  ```
- **失败兜底：** 任一子项失败会以 `{"error": "..."}` 占位，整体仍返回 200。**不要把 error 当异常抛给用户，交给后续 analyze 兜底即可。**
- **分支判断：** 若 `articles.pending_agent_pick == true`（动态站，正则采样失败），跳到**工具 2 `/rescan`**；否则跳到**工具 3 `/analyze`**。

---

## 工具 2：动态站补抓 `site_rescan`（条件分支）

- **Method / Path：** `POST /rescan`
- **何时调用：** 仅当工具 1 返回的 `articles.pending_agent_pick == true` 时调用。你（LLM）从 `articles.internal_links` 里挑出 5 篇文章 URL + 3 个产品 URL，回传给本工具补抓。
- **入参：**
  ```json
  {
    "agent_picks": {
      "articles": ["https://example.com/blog/xxx", "..."],
      "products": ["https://example.com/product/yyy", "..."]
    },
    "fetch_page": "<把工具 1 返回的 fetch 字段原样回传>"
  }
  ```
- **返回：** `{ "articles": {...}, "schema": {...} }` — **用这两个值覆盖工具 1 返回的同名字段**，再走工具 3。

---

## 工具 3：信号分析 `site_analyze`

- **Method / Path：** `POST /analyze`
- **何时调用：** 拿到完整事实后（工具 1 或工具 2 之后）。零网络、纯计算，把事实汇总成 signals / issues / baseline_scores / brand，**产出你（LLM）要写中文 findings 与 scores 的所有素材**。
- **入参（变量传递，把工具 1/2 的输出原样回传）：**
  ```json
  {
    "url":         "<工具1.url>",
    "fetch_page":  "<工具1.fetch>",
    "articles":    "<工具1.articles 或 工具2.articles>",
    "pages_audit": "<工具1.pages>",
    "schema":      "<工具1.schema 或 工具2.schema>",
    "citability":  "<工具1.citability>",
    "bots":        "<工具1.bots>",
    "technical":   "<工具1.technical>"
  }
  ```
- **返回：**
  ```json
  {
    "issues":          { "author_mismatch": [...], "homepage_missing_organization": [...], ... },
    "signals_summary": { "meta": {...}, "author": {...}, "entity": {...}, "content": {...} },
    "baseline_scores": { "citability": 62, "ai_access": 78, "technical": 55, "schema": 40, "content": 70 },
    "brand":           { "official_site": "...", "social_profiles": [...], "entity_confidence": "high" }
  }
  ```
- **接下来：** 你（LLM）基于这些素材**用中文**写 `scores.json`，结构见 `prompts/score.md`。然后调**工具 4 `/render`**。

---

## 工具 4：渲染报告 `site_render`

- **Method / Path：** `POST /render`
- **何时调用：** 最后一步。把工具 1、2、3 的全部输出 + 你写的 `scores` 打包传入，得到一份自包含的中文 HTML 诊断报告。
- **入参：**
  ```json
  {
    "domain":          "example.com",
    "url":             "<工具1.url>",
    "fetch":           "<工具1.fetch>",
    "robots":          "<工具1.robots>",
    "bots":            "<工具1.bots>",
    "citability":      "<工具1.citability>",
    "schema":          "<工具1.schema 或 工具2.schema>",
    "articles":        "<工具1.articles 或 工具2.articles>",
    "technical":       "<工具1.technical>",
    "brand":           "<工具3.brand>",
    "issues":          "<工具3.issues>",
    "signals_summary": "<工具3.signals_summary>",
    "scores":          "<LLM 写的 scores 对象，可缺省为 {}>",
    "offline":         false
  }
  ```
- **返回：** `text/html` 完整报告（单文件，75KB 左右，含 ECharts 图表）。
  - `offline: false`（默认）— ECharts 走 CDN，文件更小
  - `offline: true` — ECharts 内联，报告完全单文件可离线打开（约 1MB）
- **scores 缺省时：** 渲染器自动用确定性数据生成兜底发现，报告仍可用（评分维度会减少）。

---

## 健康检查 `site_health`

- **Method / Path：** `GET /health`
- **何时调用：** 工作流开始前确认服务可用；不需要参数。

---

## 调用顺序速查

```
[用户给 URL]
    │
    ▼
┌─────────────────┐
│ ① site_collect │
└────────┬────────┘
         │
         ├─ articles.pending_agent_pick == true ──► ② site_rescan ──┐
         │                                                          │
         └─ 否则 ─────────────────────────────────────────────────┤
                                                                  ▼
                                                          ┌───────────────┐
                                                          │ ③ site_analyze │
                                                          └───────┬───────┘
                                                                  │
                                                                  ▼
                                                       [LLM 写中文 scores.json]
                                                                  │
                                                                  ▼
                                                          ┌──────────────┐
                                                          │ ④ site_render │ ──► HTML 报告
                                                          └──────────────┘
```
