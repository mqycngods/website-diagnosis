# 02 · 数据契约

本页记录 4 个工具的**真实**入参/出参形态，以及模板替换层的坑。
凡 prompt 里写的字段名，必须与这里一致——不一致就是静默失败。

---

## 通用：请求体解析的四层兜底

`api_server.py:118 _parse_body` 依次尝试：

1. `json.loads`
2. `ast.literal_eval`（单引号 Python dict）
3. `json.loads(text.replace("'", '"'))`
4. `_loose_extract`（按键名正则切片）

**为什么要有**：平台模板用 `{{var}}` 原样替换，变量里带真实换行/未转义引号时，
拼出的 body 就不是合法 JSON。

**副作用（重要）**：一份语法错误的 `scores` 会被「救」成半残对象，
错误只体现在响应里的 `scores_parse_error` 字段，**不影响 `success: true`**。

### `_loose_extract` 的已知缺陷

`api_server.py:87-117` 假设「`plan_md` 总是最后一个键」，用 `text.rfind("}")` 截断。

而 `plan_md`（GEO 执行清单）**必然包含 JSON-LD 代码块**，也必然包含 checkbox。
`rfind("}")` 找到的是最后一处 `}`——若清单结尾还有正文，会被切掉。

> 触发条件：body 走到第 4 层兜底 + plan_md 中有 `}` 出现在结尾正文之前。
> 后果：清单静默缺尾，不报错。

---

## ① `/collect`（工具名 `diagnose_run`）

### 入参

| 字段 | 必填 | 说明 |
|---|---|---|
| `url` | 是 | 诊断目标。也接受 `inputs.url` 兜底（`api_server.py:350`） |

### 出参

```jsonc
{
  "url": "...",
  "run_id": "a1b2c3d4e5f6",
  "fetch": {
    "url","status_code","title","description","canonical","meta_tags",
    "h1_tags","heading_structure","word_count","text_content",
    "internal_links","external_links","images","structured_data",
    "security_headers","freshness","has_ssr_content",
    "challenge_detected","errors"
  },
  "robots":     {"exists", "ai_crawler_status": {bot: "ALLOWED|BLOCKED|BLOCKED_BY_WILDCARD"}},
  "technical":  {...},
  "bots":       {"overall_score", "probes": [{bot, class, blocked, ...}], "robots_cap_applied"},
  "citability": {"average_citability_score", "paragraphs": [...]},
  "articles":   {
    "sampled": [...], "sampled_products": [...],
    "pages": [...], "product_pages": [...],
    "pending_agent_pick": true,
    "internal_links": [...],
    "list_pages_expanded": bool   // 仅展开过列表页时出现
  },
  "pages":  {"found": {...}, "missing": [...]},
  "schema": {...},
  "_meta":  {"elapsed", "partial", "completed": [...], "timed_out": [...]}
}
```

### 载荷体积（实测，housebell.com-20261102-171914）

| 字段 | 字符数 | 备注 |
|---|---|---|
| `text_content` | 57,164 | **最大单项**，原样返回，未截断 |
| `images` | 37,167 | 每个 img 的 src/alt/width/height |
| `internal_links` | 2,186 | |
| `structured_data` | 1,972 | |

> `text_content` 与 `images` 占了 fetch 的绝大部分，而后续步骤只用到
> `word_count` / `has_ssr_content` / `freshness` / `structured_data`。
> 这是 prompt 上下文预算的主要消耗点（见 [05 优化](05-optimization-roadmap.md#裁载荷)）。

### 超时语义

`pipeline.fetch_all(url, soft_timeout)`：
- 阶段 1 `fetch_core` 必跑（首页 + robots + technical）
- 阶段 2 `probe_bots ∥ scan_pages`，等待 `remaining = soft_timeout - elapsed`
- 超时子任务写 `{"error": "timeout", "partial": true}`，整体仍 200
- `_meta.partial = true`，`_meta.timed_out = [...]`

**注意**：`_meta` 在 prompt 里**完全没有出现**。模型不知道数据是残缺的，
会照常给出评分——这是「部分采集被当成完整结论」的风险源。

---

## ② `/rescan`（工具名 `site_rescan`）

### 入参（平铺，非嵌套）

| 字段 | 必填 | 形态 |
|---|---|---|
| `run_id` | 是 | 字符串 |
| `articles` | 是 | **原始 JSON 数组文本**，如 `["https://a.com/blog/p1"]` |
| `products` | 是 | 同上 |

兼容旧形态 `agent_picks: {articles, products}`（`api_server.py:390-395`）。

### 坑：双重引号

工具声明要求传「JSON 数组文本，不要整体再加一层引号」。
若传 `"[\"https://...\"]"`（字符串化两次），`_as_json` 解析一次得到字符串而非 list，
`pipeline.rescan_pages` 会拿到非列表输入。

### 出参

`{articles, schema, run_id}`，且**已增量合并写回暂存**（`api_server.py:399-427`）：

```python
stored["articles"] = {
    "sampled":          merge_unique(prev, new),
    "sampled_products": merge_unique(prev, new),
    "pages":            merge_pages(prev, new),
    "product_pages":    merge_pages(prev, new),
    "internal_links":   prev,          # 不覆盖
    "pending_agent_pick": False,       # 合并后置 false
}
stored.pop("p0_blockers")             # 强制失效，render 会重算
```

> 为什么是增量而非覆盖：`api_server.py:400-407` 记录了 pockella.com 案例——
> 覆盖会清掉 collect 阶段已采到的样本，导致「全站无机器可读日期」误报。

---

<a id="analyze-endpoint"></a>

## ③ `/analyze`（工具名 `site_analyze`）

### 入参

| 字段 | 必填 | 说明 |
|---|---|---|
| `run_id` | 是 | 从暂存取 fetch/articles/pages/schema/citability/bots/technical/robots |
| `url` | 是 | 兜底（暂存 url 为空时用） |

无 run_id 时必须自带采集数据，否则 422（`api_server.py:483-489`）。

### 出参

```jsonc
{
  "issues":     {"<analyzer_name>": [{kind, severity, title, evidence, category}, ...]},
  "signals_summary": {...},
  "baseline_scores": {...},   // 确定性基线分，非 LLM 评分
  "brand": {...},
  "p0_blockers": [            // ← 唯一裁判，见 03 文档
    {"key", "class_key", "class_name", "title", "evidence", "confidence"}
  ]
}
```

### `p0_blockers` 是设计核心

由 `diagnose/blockers.py:138 compute_p0_blockers` 计算，
输入是 `fetch/bots/schema/issues/articles/robots`，**不依赖 LLM**。
报告横幅、执行清单前置判定、评分 Critical 对齐三处消费同一份结果。

详见 [03 P0 判定口径](03-p0-and-scoring.md)。

---

## ④ `/render`（工具名 `site_render`）

### 入参（4 个全必填）

| 字段 | 形态 | 注意 |
|---|---|---|
| `run_id` | string | 暂存过期时靠 url 兜底 |
| `url` | string | 必带，为空直接 400（`api_server.py:595-615`） |
| `scores` | **JSON 字符串** | 不是对象。无评分传 `"{}"` |
| `plan_md` | Markdown 字符串 | 原文，不摘要 |

请求头是 `Content-Type: text/plain`（`agent.json:216`）——为规避 Render WAF 误拦。

### 出参

```jsonc
{
  "success": true,
  "html": "整份报告 HTML 全文",       // ← 体积 112K–145K
  "size": 116251,
  "domain", "url", "filename", "result_dir",
  "report_path", "report_url",        // 公开可访问
  "md_path", "md_url",                // plan_md 为空时均为 null
  "is_fallback": false,
  "error": null,
  "scores_parse_error": null          // 非 null = scores 有问题
}
```

### 失败分支（`api_server.py:641-688`）

抓到异常时返回：

```jsonc
{"success": false, "html": "<兜底 HTML，含 traceback>", "is_fallback": true, "error": "..."}
```

**缺失字段**：`report_url`、`md_url`、`result_dir` 都不存在。
prompt 要求此时 `html_url`/`md_url` 填空字符串并判整次失败——逻辑自洽。

**安全隐患**：兜底 HTML 里嵌了 `traceback.format_exc()` 全文
（`api_server.py:643,675`），随响应回传给 LLM 并可能进入最终产物。
见 [04 脆点](04-fragility-map.md#info-leak)。

### `scores_parse_error` 的语义

`api_server.py:536-547`：

| 情形 | 结果 |
|---|---|
| `scores` 为空 | 无报错，走确定性兜底 findings |
| 解析后不是 dict | `scores_parse_error = "scores 不是 JSON 对象"` |
| 解析后为空 dict 但原文非空 | `scores_parse_error = "scores 无法解析为 JSON（可能含尾逗号/未转义引号）"` |

**关键**：以上三种都仍会 `success: true`。prompt 只把它写进「重试条件」，
没写进「放弃」条件——最坏情况是带着残缺评分交付。

---

## ⑤ 最终输出契约（第 7 步）

平台按 `$[0].result`、`$[0].html_url`、`$[0].md_url`、`$[0].html_result` 取值。

```json
[
  {
    "result": "<执行清单 Markdown 全文>",
    "html_url": "<report_url>",
    "md_url": "<md_url>",
    "html_result": "<html 全文，必须最后>"
  }
]
```

- **必须是一个数组、一个对象、四字段同层**。拆成多个对象 → 判缺字段。
- `html_result` 放最后 → 触及输出上限时先截断它自己，不伤及短字段。
- 这也是**为什么它是最脆的一步**：见 [04 脆点](04-fragility-map.md#html-result)。
