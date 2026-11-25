# 08 · 速查卡

一页纸。改代码前扫一眼。

---

## 链路

```
diagnose_run(/collect) → site_rescan(/rescan) → site_analyze(/analyze)
   → LLM: scores.json → LLM: plan_md → site_render(/render) → LLM: 最终 JSON
```

## 关键常量

| 常量 | 值 | 位置 |
|---|---|---|
| `_STORE_TTL` | 2h | `api_server.py:169` |
| `_STORE_MAX` | 50（仅 memory） | `api_server.py:170` |
| `soft_timeout` (collect) | 60s | `api_server.py:354` |
| `_THIN_WORDS` | 50 | `blockers.py:59` |
| `_THIN_WORDS_WITH_NO_HEADINGS` | 150 | `blockers.py:61` |
| `_FRESHNESS_MIN_SAMPLES` | 3 | `blockers.py:70` |
| `polite_delay` | 1s | `config/*.yaml` |
| `sample_articles` / `sample_products` | 5 / 3 | `config/*.yaml` |

## 五维权重

```
citability 31.25% · content 25% · technical 18.75% · schema 12.5% · platform 12.5%
```

## P0 能触发的 key（白名单）

**deterministic**（可进 P0）：
```
fetch_failed · no_ssr · retrieval_bots_blocked · robots_disallow_all
homepage_zero_jsonld · consistency:author_mismatch_template
```

**inferred**（只列「疑似」，不定 P0）：
```
challenge_page · thin_content · no_heading_structure · no_machine_readable_date
```

**永不为 P0**（最高 High）：
```
sameAs 为空 · 全站无 Organization · 主页缺 Organization · 有正文但可引用性低 · 任何新鲜度问题
```

## 封闭词表（写错即英文外露）

```
severity : Critical | High | Medium | Low
confidence: Confirmed | Likely | Hypothesis
layer    : domain | template | editorial | page
impact   : High | Med | Low          ← Med 不是 Medium
effort   : minutes | hours | days | weeks
owner    : developer | content | marketing
```

## 工具契约要点

| 工具 | 入参要点 | 出参要点 |
|---|---|---|
| `diagnose_run` | `{url}` | 返回 `run_id` + 全部采集 |
| `site_rescan` | `{run_id, articles(JSON文本), products(JSON文本)}` | 增量合并，pop `p0_blockers` |
| `site_analyze` | `{run_id, url}` | `p0_blockers` 是唯一裁判 |
| `site_render` | `{run_id, url, scores(字符串), plan_md}` 四必填 | `success` 判成败；`scores_parse_error` |

## 最终输出格式

```json
[{"result": "...", "html_url": "...", "md_url": "...", "html_result": "..."}]
```

一个数组、一个对象、四字段同层、`html_result` 最后。

## 三条红线

1. **不要让 LLM 搬运 HTML**——`html_result` 是主失败源（11–14.5 万字符）。
2. **不要改 `prompts/score.md` 期望线上生效**——agent 读的是 `skill_resource/`。
3. **不要在 findings 里对 sameAs/Organization 空白打 Critical**——会被降级，
   且说明你违反了评分卡口径。

## 排障最短路径

| 症状 | 先查 |
|---|---|
| 判失败/缺字段 | `site_render` 响应原文，`success` 与 `scores_parse_error` |
| 横幅与清单矛盾 | `p0_blockers` vs `findings[].kind` |
| 全站无日期误报 | `articles.pages` 是否采到栏目页 |
| run_id 找不到 | `STORE_BACKEND` 是否 memory + 多副本 |
| 改了 prompt 没效果 | 改的是 `score.md` 还是 `skill_resource/` |
