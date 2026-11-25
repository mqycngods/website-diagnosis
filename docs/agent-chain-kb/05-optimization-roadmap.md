# 05 · 优化路线

按「收益 / 改动成本」排序。每项给出：问题、方案、改动位置、验收标准、风险。

---

## P0 优先：值得先做

### 1. `html_result` 不再由 LLM 搬运

<a id="1-html-result"></a>

**问题**：见 [04 #1](04-fragility-map.md#html-result)。

**方案**（三选一，推荐 A）：

| 方案 | 做法 | 改动 |
|---|---|---|
| **A. 下游按 URL 取** | `html_result` 改为可选；平台/下游用 `report_url` 拉取 | schema + prompt + 下游 |
| B. 只回传校验和 | `html_result` = `{sha256, size}`；完整性由下游校验 | schema + prompt |
| C. 服务端压缩 | render 同时返回 gzip+base64，体积降 80%+ | `api_server.py` |

A 最彻底：报告已落盘到 `results/<dir>/`，`report_url` 公开可访问
（`api_server.py:691` 的 `/results/{dir}/{file}`），根本不需要搬运。

**验收**：
- 最终输出 JSON 长度 < 5,000 字符
- 端到端成功率提升（对比改造前后 20 次诊断）
- `report_url` 可公开访问且内容与 `size` 一致

**风险**：若有下游依赖 `html_result` 字段（如入库），需同步改造。
建议先保留字段但允许传空 + `report_url`，灰度一轮。

---

### 2. 裁 `text_content` / `images` 载荷

<a id="裁载荷"></a>

**问题**：`/collect` 返回的 `fetch` 中，`text_content`（实测 57K 字符）
与 `images`（37K 字符）占绝大部分，而后续步骤只用：

- `word_count`、`has_ssr_content`、`freshness`、`structured_data`
- `text_content` 仅用于 LLM 复核日期（`website_audit_prompt.md:71`）

**方案**：在 `/collect` 响应里对 `fetch` 做裁剪，或额外提供精简视图：

```python
# 保留用于 freshness 复核的前 N 字符 + 尾部 N 字符（页脚常有版权年份）
fetch["text_content"] = text[:4000] + ("…" if len(text) > 8000 else "") + text[-2000:]
fetch["images"] = fetch["images"][:30]        # 只留前 30 个
```

**注意**：`text_content` 的**尾部**必须保留——页脚版权年份是 freshness 复核的关键，
而 prompt 明确要求排除 UGC 自述时间（`website_audit_prompt.md:71`）。

**改动位置**：`diagnose/pipeline.py:57 fetch_core` 之后，或 `api_server.py:340 collect`。

**验收**：
- `/collect` 响应 < 40KB（当前最高约 100KB+）
- freshness 复核逻辑不变（用 `tests/` 里相关用例验证）

**风险**：若 LLM 依赖正文中段内容做判断（如「全文无一句完整陈述句」判定），
裁剪会影响判定质量。建议保留首尾 + 中间均匀采样。

---

### 3. `pending_agent_pick` 改为按需触发

<a id="pending-agent-pick"></a>

**问题**：见 [04 #4](04-fragility-map.md#pending-agent-pick)。

**方案**：`diagnose/pipeline.py:204` 改为：

```python
no_details_found = not article_urls and not product_urls
articles = {..., "pending_agent_pick": no_details_found, ...}
```

即：只有服务端**确实一个详情页都没采到**（含 sitemap 兜底失败）时才让 agent 参与。

**配套**：
- `website_analyze-agent.md:11` 的第 2 步条件已经写的是
  「仅当 `articles.pending_agent_pick == true`」——无需改
- 可删除 `website_analyze-agent.md:17` 中大段「不要传列表页」的说教
  （服务端已展开列表页 + sitemap 兜底，LLM 挑到列表页的概率大幅下降）

**验收**：
- 常规站点（有 /blog/<slug> 内链）不再触发 rescan
- SPA/无内容页站点仍正确触发
- `tests/` 中相关用例通过

**风险**：低。服务端采样能力已经足够（三层兜底），agent 主要价值是
「人工判断内容页 vs 列表页」，而这已经被 `pick_article_urls` 的模式匹配覆盖。

---

## P1：结构性改进

### 4. prompt 单一真源

<a id="单一真源"></a>

**问题**：见 [04 #2](04-fragility-map.md#prompt-multi-source)。

**方案**（二选一）：

| 方案 | 做法 | 适用 |
|---|---|---|
| A. 生成式 | 以 `score.md` 为真源，写脚本生成 skill 版的精简视图 | 保守，保留两份但保证一致 |
| B. 反向同步 | 以 skill 版为真源，删掉 `score.md`/`SKILL.md` 的重复内容 | 激进，单一文件 |

**推荐 A**：保留「完整版」（给人工阅读/其他平台）+「精简版」（注入 agent），
用生成脚本 + CI 校验：

```bash
# CI 里
python scripts/gen_skill_prompts.py --check
# 若生成的 skill 文件与仓库中的不一致 → 失败
```

**验收**：
- 修改 `score.md` 后跑生成脚本，skill 文件随之更新
- CI 检查能捕获手工改 skill 文件的行为

**风险**：低。但需要先把两者内容对齐一次（现在 skill 版有 `kind`，
`score.md` 没有；`score.md` 有协议权重表，skill 版没有）。

---

### 5. 机械规则下沉服务端

<a id="机械规则"></a>

**问题**：见 [04 #3](04-fragility-map.md#prompt-unverifiable)。

**方案**：在 `/render` 前增加一层校验，把能机械判定的规则做掉：

| 规则 | 能否机械化 | 建议 |
|---|---|---|
| 封闭词表（severity/impact/effort/owner） | ✅ 完全 | 校验，非法值修正 + 记 warning |
| `kind` 覆盖（每条 deterministic blocker 对应 finding） | ✅ 完全 | 缺失时自动补 finding skeleton |
| 跨数组动作去重（quick_wins/medium_term/strategic） | ✅ 完全 | 文本相似度去重 |
| 数量区间（findings 6–10，任务 8–14） | ✅ 完全 | 超限告警，不阻断 |
| 根因归并 | ⚠️ 半自动 | 只做标题相似度提示 |
| 原子性拆分 | ❌ 需判断 | 留 prompt |
| 排序合理性 | ❌ 需判断 | 留 prompt |

**改动位置**：新增 `render/validate_scores.py`，在 `api_server.py:539` 之后调用。

**关键设计**：校验结果要**回传**给模型（放进 `/render` 响应），
否则模型不知道自己违反了规则——当前 `cap_severity_p0` 的问题正是纠正不回传。

```jsonc
{
  "success": true,
  "warnings": [
    {"code": "invalid_enum", "field": "findings[2].severity", "value": "Severe", "fixed": "High"},
    {"code": "missing_kind", "blocker": "no_ssr"}
  ]
}
```

**验收**：
- 故意传非法 severity，响应里有 warning 且报告渲染正确
- 故意漏掉一条 blocker，响应里有 warning

**风险**：中。自动修正可能与模型意图不符，建议「修正 + 告警」而非「阻断」。

---

## P2：配置与安全

### 6. 参数收敛

**改动**：`website_analyze-agent.json`

| 字段 | 现值 | 建议 | 理由 |
|---|---|---|---|
| `temperature` | 0.3 | **0** | 链路要求确定性；开放题才需要随机性 |
| `max_retries` | 3 | **1** | 重试会重新抓取，产物不可复现；1 次足够 |
| `timeout_seconds` | 1200 | **600** | 配合 #1 改造后，输出量大幅下降 |

**验收**：连续 10 次同 URL 诊断，scores 结构一致（内容可因抓取时点不同而变）。

---

### 7. 密钥移出 ZIP

**方案**：
- 平台侧工具 `auth_type` 改为从 secret 注入
- 或将 Key 放进部署环境变量，`agent.json` 里留占位符

**验收**：解压 ZIP 后 `grep -r "stpTeS"` 无命中。

**风险**：需协调平台侧配置方式。若平台不支持 secret 注入，
至少把 Key 换成一个可轮换的、权限受限的专用 key。

---

### 8. 兜底 HTML 去 traceback

**改动**：`api_server.py:643-677`

```python
# 改：错误只留类别，traceback 只进服务端日志
fallback_html = ... f"<p>{type(e).__name__}: 渲染失败</p>" ...
print(f"[render] 渲染失败: {e}\n{traceback.format_exc()}")  # 日志保留
```

**验收**：构造一次渲染失败，响应 `html` 里不含 `File "` 或 `.py` 路径。

---

### 9. 修 `_loose_extract` 的 `plan_md` 截断

**改动**：`api_server.py:108`

```python
# 改：plan_md 是最后一个键，直接取到结尾（去掉末尾可能的 }
if key == "plan_md":
    val = text[val_start:].rstrip()
    if val.endswith("}"):
        val = val[:-1]
    val = val.rstrip().rstrip('"')
```

**更稳的做法**：既然 `plan_md` 是最后一个键，不应该用 `rfind("}")`，
而应该「去除末尾的 `"}` 包裹」后原样保留。

**验收**：构造一个含 JSON-LD 代码块且结尾有正文的 plan_md，
走 `_loose_extract`，内容完整。

---

### 10. 打包清理

**做法**：新增 `scripts/pack_agent.sh`：

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
find website_analyze -name '.DS_Store' -delete
rm -f website_analyze.zip
zip -r website_analyze.zip website_analyze -x '*/__MACOSX/*' -x '*/.DS_Store'
```

**验收**：`unzip -l website_analyze.zip | grep -c "DS_Store\|__MACOSX"` 为 0。

---

## P3：可选

### 11. `_meta.partial` 透传到 prompt

`website_analyze-agent.md` 加一句：

> 若 `_meta.partial == true`，说明部分采集超时。报告中相关章节必须写「未测量」，
> 且不得对该项给出评分依据。

### 12. 两代模板产物归档

`results/` 里 57 个目录、最大 1.2MB/份。建议：
- 保留最近 10 次作为回归样本
- 其余归档到对象存储或直接清理

---

## 执行建议顺序

```
① #1 html_result      ← 单点收益最大，先做
② #6 参数收敛          ← 与 ① 配套，几乎零成本
③ #3 pending_agent_pick ← 减少一次往返，可删大段 prompt
④ #2 裁载荷           ← 降上下文消耗
⑤ #5 机械规则         ← 需要新增模块，收益长期
⑥ #4 单一真源         ← 需要先对齐内容
⑦ #7~#10 配置与安全    ← 可并行
```
