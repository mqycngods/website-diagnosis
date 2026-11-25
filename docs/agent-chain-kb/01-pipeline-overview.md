# 01 · 链路总览

## 全景

```
用户输入 url
    │
    ▼
[平台 LLM] 载入 system prompt：website_analyze-agent.md (9.2K 字)
    │
    ├─① diagnose_run ──────────► POST /collect
    │                             pipeline.fetch_all(url, soft_timeout=60s)
    │                             └ fetch_core → (probe_bots ∥ scan_pages)
    │                             └ 结果按 run_id 暂存服务端（TTL 2h）
    │                             ◄ {run_id, fetch, robots, technical, bots,
    │                                citability, articles, pages, schema, _meta}
    │
    ├─② site_rescan（条件）────► POST /rescan
    │   条件：articles.pending_agent_pick == true（**实际恒为 true**）
    │   LLM 从 internal_links 挑 3–5 文章 + 2–3 产品 URL
    │   ◄ {articles, schema} 并**增量合并**写回暂存
    │
    ├─③ site_analyze ──────────► POST /analyze
    │                             纯计算，零网络：signals → analyzers → baseline
    │                             └ compute_p0_blockers(...)  ← 唯一裁判
    │   ◄ {issues, signals_summary, baseline_scores, brand, p0_blockers}
    │
    ├─④ [LLM] 读 skill website_audit (17.5K 字) → 写 scores.json（自由文本）
    │
    ├─⑤ [LLM] 读 skill execution_plan (9.6K 字) → 写 8–14 项 Markdown 清单
    │
    ├─⑥ site_render ───────────► POST /render
    │   body = {run_id, url, scores(json字符串), plan_md}
    │   └ build_report_from_dict → render_html_string → 落盘 results/<dir>/
    │   ◄ {success, html, size, report_url, md_url, scores_parse_error, ...}
    │
    └─⑦ [LLM] 输出 JSON 数组，四字段同一对象
        {result, html_url, md_url, html_result}
        └ html_result = site_render 返回 html 的**逐字全文**
```

## 步骤详表

| 步 | 载体 | 耗时量级 | 失败语义 |
|---|---|---|---|
| ① | `POST /collect` | 30–60s（软超时 60s，超时返回 partial） | 子任务失败写成 `{"error": ...}`，整体仍 200 |
| ② | `POST /rescan` | 逐页抓取，`polite_delay=1s` × 页数 | 同上；无 run_id 且无 fetch_page → 422 |
| ③ | `POST /analyze` | <1s（纯计算） | 无 run_id 且无采集数据 → 422 |
| ④ | LLM | 取决于上下文 | **无校验**，写错只有 render 层兜 |
| ⑤ | LLM | 取决于上下文 | 无校验 |
| ⑥ | `POST /render` | 1–3s | 异常时返回 `success:false` + 兜底 HTML |
| ⑦ | LLM | **与 html_result 长度线性相关** | 截断即被判缺字段 |

## 关键设计：双脑架构

`website_analyze-agent.md:3-6` 明确定义：

- **工具做确定性的事**：抓取、robots 解析、12 个爬虫 UA 重放、段落可引用性评分、
  Schema 检测、渲染。同一 URL 任何机器结果一致。
- **LLM 做判断与写作**：评分、中文 findings/actions、任务拆分与排序。

这条边界的意义：**凡是能用代码判定的，都不该进 prompt 让 LLM 裁量**。
当前边界正在被侵蚀——`website_audit_prompt.md` 里有 9 组「硬性自检」，
服务端一条都不校验（见 [04 脆点](04-fragility-map.md#prompt-unverifiable)）。

## run_id 生命周期

```
collect  → _store_put(run_id, {...})          # 写 1
rescan   → _store_put(run_id, merged)         # 写 2（增量合并 articles/schema）
analyze  → _store_put(run_id, merged+brand+issues+p0_blockers)  # 写 3
render   → _store_get(run_id)                 # 只读
```

- TTL `_STORE_TTL = 2h`（`api_server.py:169`）
- memory 后端容量 `_STORE_MAX = 50`，超限 FIFO 淘汰
- **多副本部署必须用 Redis**（`STORE_BACKEND=redis`），否则跨请求丢 run_id

render 会读 `stored["p0_blockers"]`（`api_server.py:566`）——注意 rescan 会
`stored.pop("p0_blockers")` 强制失效（`api_server.py:423`），因为补抓改变了 articles。

## 阅读顺序建议

- **接手**：本页 → [03 P0 口径](03-p0-and-scoring.md) → [06 排查](06-troubleshooting.md)
- **改 prompt**：本页 → [02 契约](02-data-contracts.md) → [04 脆点](04-fragility-map.md)
- **排故障**：直接跳 [06 排查](06-troubleshooting.md)
