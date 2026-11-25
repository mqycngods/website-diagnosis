# 04 · 脆点清单

按「失败代价 × 触发概率」排序。每条给出：现象、根因、位置、当前是否已缓解。

---

<a id="html-result"></a>

## 1. `html_result` 逐字誊写 —— 主失败源

**现象**：agent 最终输出的 `html_result` 被截断/省略，平台判「缺少字段」，
整次诊断作废，触发 agent 级重试（重新抓取 → 结果不可复现）。

**根因**：prompt 要求 LLM 把整份 HTML 复制进 JSON 字符串
（`website_analyze-agent.md:58`）。这是**用生成模型做字节搬运**。

**实测规模**（当前模板，`results/` 最近三次运行）：

| 站点 | HTML 字符数 |
|---|---|
| dianclaw.cn | 116,251 |
| creativehit.ai | 112,467 |
| pockella.com | 144,811 |

**为什么必输**：输出上限与生成长度线性相关；即使不截断，1200s 超时下
百万级 token 输出也无法完成。且 prompt 对「截断」只有一句「禁止」，无机制保证。

> **历史对比**：旧模板（hglcn.com，2026-11-05）单份 1,238,984 字符，
> 其中内联 ECharts 库 1,024,031 字符。当前模板已改为 CDN 引入，
> 体积降到 1/10——但相对输出上限仍然是巨额。

**缓解状态**：未缓解。见 [05 优化 #1](05-optimization-roadmap.md#1-html-result)。

---

<a id="prompt-multi-source"></a>

## 2. prompt 多源漂移

**现象**：同一规则在仓库里有多份拷贝，改一处不改另一处，评分口径分叉。

**现状**：

| 文件 | 字符 | 角色 |
|---|---|---|
| `website_analyze/skill_resource/website_audit_prompt.md` | 17,554 | **agent 实际执行** |
| `prompts/score.md` | 33,260 | 自称「真源」，实际未注入 |
| `website_analyze/skill_resource/execution_plan_prompt.md` | 9,564 | **agent 实际执行** |
| `SKILL.md` | 31,188 | 自称真源，实际未注入 |

skill 文件开头都写着「本文件是仓库 X 的自包含版本」，但内容已明显不同：

- `score.md` 有 `协议权重表`、`电商 ItemList 规范`、`行业自适应外链权威度`、
  `FAQ/Contact 防误报细则`——skill 版都没有
- skill 版有 `kind` 字段对账规则——`score.md` 没有

**后果**：修改 `score.md` 不会影响线上 agent。维护者若以为改的是真源，
线上行为不会变。

**缓解状态**：未缓解。

---

<a id="prompt-unverifiable"></a>

## 3. prompt 规则不可校验

**现象**：`website_audit_prompt.md` 里有 9 组「硬性自检」，服务端一条都不校验。

清单：

1. 封闭词表（6 字段）
2. 五类 Critical 判定 + 排除边界
3. `p0_blockers` 同源对齐
4. `kind` 字段必填映射
5. 根因归并 4 条（含跨维度去重）
6. 原子性拆分（耗时差 >5 倍拆开）
7. quick_wins 三键排序 + 跨数组去重
8. medium_term 拓扑排序
9. Critical 双向覆盖自检

**后果**：写错了只有 render 层的 `cap_severity_p0` 能兜住 severity，
其余（去重、排序、数量区间、跨数组动作重复）**全部静默通过**。
而 `cap_severity_p0` 的纠正也不会回传给模型。

**缓解状态**：部分（仅 severity）。

---

<a id="pending-agent-pick"></a>

## 4. `pending_agent_pick` 恒为 true

**位置**：`diagnose/pipeline.py:204`

```python
articles = {"sampled": article_urls, "sampled_products": product_urls,
            "pages": [], "product_pages": [],
            "pending_agent_pick": True, "internal_links": internal_links}
```

**现象**：每次诊断都强制触发 `site_rescan`，多一次工具往返。

**为什么是设计缺陷**：
- 服务端已做三层自动采样（`pick_article_urls` / 列表页展开 / sitemap 兜底）
- 服务端挑不出的（内链里没有详情页），agent 从同一份 `internal_links` 里也挑不出

**衍生成本**：
- prompt 花大段篇幅教模型「不要传列表页」（`website_analyze-agent.md:17`）
- `rescan` 的增量合并逻辑、pockella.com 误判案例的修复复杂度和维护成本
- 每多一次工具调用就多一次失败可能

**缓解状态**：未缓解。

---

<a id="partial-collection"></a>

## 5. 部分采集被当成完整结论

**现象**：`/collect` 超时时返回 `_meta.partial = true`，
但 prompt 里**完全没有提到 `_meta`**。

**后果**：模型拿到残缺的 `bots`/`citability`（`{"error": "timeout", "partial": true}`），
照常给出评分和 findings，报告里不体现「未测量」。

虽然 prompt 有「工具返回 `{"error":...}` 不中断，继续下一步；报告对应章节写「未测量」」
（`website_analyze-agent.md:62`），但 `_meta.partial` 是**整体标记**，
不是单个字段的 `error`——模型无法感知哪些字段是超时导致的空。

**缓解状态**：未缓解。

---

<a id="loose-extract-truncate"></a>

## 6. `_loose_extract` 静默截断清单

**位置**：`tools/api_server.py:108`

```python
val = text[val_start:text.rfind("}") if "}" in text else len(text)]
```

**现象**：`plan_md`（GEO 执行清单）必然包含 JSON-LD 代码块，
`rfind("}")` 会切掉最后一处 `}` 之后的正文。

**触发条件**：body 走到第 4 层解析兜底（即前 3 层都失败）。

**后果**：清单静默缺尾，`success` 仍为 `true`。

**缓解状态**：未缓解。

---

<a id="scores-parse-error"></a>

## 7. `scores_parse_error` 不阻断

**位置**：`api_server.py:536-547`

**现象**：scores 解析失败时，`success` 仍为 `true`，只把错误写进
`scores_parse_error` 字段。

**prompt 侧**（`website_analyze-agent.md:63`）只把它写进「重试条件」：

> 若上一次失败是响应体的 `scores_parse_error` 非空…不要原样重发

但这是「失败后的重试指引」——如果 render 返回 `success: true` +
`scores_parse_error` 非空，prompt 没有任何指令要求此时重试或判失败。

**后果**：用户拿到一份丢了一半 findings 的报告（走了确定性兜底）。

**缓解状态**：未缓解。

---

<a id="packaging"></a>

## 8. 打包

**现象**：`website_analyze.zip`（27,447 bytes）内含：

- `.DS_Store`（6,148 bytes，占 22%）
- `__MACOSX/._*` 资源叉文件（每个文件一份）

**后果**：包体虚增，且 `.DS_Store` 可能含本地路径信息。

**验证方式**：

```bash
unzip -l website_analyze.zip
```

**缓解状态**：未缓解。打包前应 `find . -name '.DS_Store' -delete` 并排除 `__MACOSX`。

---

<a id="api-key-leak"></a>

## 9. 密钥外泄

**位置**：`website_analyze-agent.json:95,136,169,215`

```json
"headers": {"X-API-Key": "", ...}
```

**现象**：API Key 明文写在 4 个工具的 headers 里。ZIP 包会被分发/导入，
密钥随包外泄。

`README.md` 3.7 说「敏感字段导出时会被过滤」——但这里是**明文**，
不是 `auth_config` 结构，平台不会过滤。

**缓解状态**：未缓解。

---

<a id="info-leak"></a>

## 10. 信息外泄

**位置**：`api_server.py:643, 675`

```python
err_detail = traceback.format_exc()
...
<pre>{err_detail}</pre>
```

**现象**：render 失败时的兜底 HTML 里嵌入完整 traceback，
随 `html` 字段回传给 LLM，可能进入最终产物或报告。

**后果**：暴露内部文件路径、函数名、代码行。

**缓解状态**：未缓解。

---

<a id="param-config"></a>

## 11. 参数配置偏保守

| 参数 | 当前 | 问题 |
|---|---|---|
| `temperature` | 0.3 | 链路要求高度确定性（封闭词表、JSON 合法性、逐字誊写），0.3 是纯风险 |
| `max_retries` | 3 | 每次重试重新抓取，产物不可复现；3 次 × 20 分钟远超合理预算 |
| `timeout_seconds` | 1200 | 与 `max_retries: 3` 组合理论上限 1 小时 |

**缓解状态**：未缓解。

---

<a id="template-generations"></a>

## 12. 两代模板并存

**现象**：`results/` 里的历史产物有两种形态：

| 代际 | 图表数据 | `report-data` 块 | 单份体积 |
|---|---|---|---|
| 旧（截至 2026-11-05） | `id="chart-data"` 块 | **有**（最大 100K 字符） | 282K–1.24M |
| 新（2026-11-18 起） | `window.__CHART_DATA__` | 无 | 112K–145K |

**影响**：
- 旧报告里的 `report-data` **无任何 JS 读取**（全部样本中
  `getElementById("report-data")` 命中数为 0），是纯冗余
- 当前模板已修复此问题
- **分析时不要拿旧报告下结论**——体积问题已大幅缓解

**缓解状态**：已修复（模板侧）。残留的历史产物仅作证据。
