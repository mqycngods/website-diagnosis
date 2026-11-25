# 03 · P0 判定与评分口径

> 这是全链路最容易被改坏的地方。改任何一处前，先读完本页。

## 为什么需要「唯一裁判」

历史上阻塞判定分散在三处，各自独立判断：

1. 评分 LLM 按「Critical 五类」自己判
2. 执行清单 LLM 自己写前置判定
3. 渲染层数 Critical finding 的条数画横幅

**三者必然漂移**。真实事故（`diagnose/blockers.py:1-8` 记录）：
同一次诊断里报告写「可直接优化」，清单写「存在阻塞」，红色 P0 卡片同时出现。

修法是：把判定从实测数据直接算出来，三处都消费同一份 `p0_blockers`。

---

## 唯一裁判：`compute_p0_blockers`

位置：`diagnose/blockers.py:138`

```python
compute_p0_blockers(*, fetch, bots, schema, issues, articles, robots=None) -> list
```

输出每个 blocker：

```json
{"key": "...", "class_key": "...", "class_name": "...",
 "title": "...", "evidence": "...", "confidence": "deterministic|inferred"}
```

### 五类阻塞

| 类 | class_key | 触发 key | confidence |
|---|---|---|---|
| ① 抓取通道被切断 | `access` | `fetch_failed` | deterministic |
| ① | `access` | `no_ssr` | deterministic |
| ① | `access` | `retrieval_bots_blocked` | deterministic |
| ① | `access` | `robots_disallow_all` | deterministic |
| ① | `access` | `challenge_page` | **inferred** |
| ② 抓到但读不出 | `readability` | `thin_content` | **inferred** |
| ② | `readability` | `no_heading_structure` | **inferred** |
| ③ 读懂但不敢引用 | `trust` | `consistency:author_mismatch_template` | deterministic |
| ④ 实体锚定断裂 | `entity` | `homepage_zero_jsonld` | deterministic |
| ⑤ 时效性 | `freshness` | `no_machine_readable_date` | **inferred** |

### 抑制链（原则 2：一个病根只报一条）

```
fetch_failed  → ②③④⑤ 全部短路
no_ssr        → 抑制 thin_content / homepage_zero_jsonld / no_machine_readable_date
```

原因：无 SSR 必然薄、必然零 JSON-LD、必然无日期——同根因不重复列。

### 门槛（原则 1：宁漏勿判）

- 临界值附近的命中，evidence 里必须写具体数值
- 样本不足不判：`no_machine_readable_date` 要求至少 3 个有效页面（`_FRESHNESS_MIN_SAMPLES`）
- 能触发 P0 的 issue kind 用白名单封闭枚举（`_CONSISTENCY_BLOCKER_KINDS`），
  新 kind 默认只算 High

### 阈值

| 常量 | 值 | 用途 |
|---|---|---|
| `_THIN_WORDS` | 50 | 正文低于此值 → thin_content |
| `_THIN_WORDS_WITH_NO_HEADINGS` | 150 | 低于此值且无标题 → no_heading_structure |
| `_FRESHNESS_MIN_SAMPLES` | 3 | 无日期推断的最小样本 |

---

## 明确的边界（**不要过度判定**）

这些情形**不是** P0，即使分析器报了 Critical：

| 情形 | 定级 | 理由 |
|---|---|---|
| 首页有 JSON-LD 但只有 BreadcrumbList | High | 管道已建立，缺的只是声明丰富度 |
| 全站无 Organization | High | AI 可从正文/外链/知识图谱多源识别 |
| 主页缺 Organization 但子页有 | High | 权重分散，不切断引用通路 |
| Organization 存在但 sameAs 为空 | High | 削弱图谱对齐，不切断引用 |
| 有实质正文但可引用性低 | High | 内容质量问题，AI「不想引」而非「读不到」 |
| 任何新鲜度问题 | High 封顶 | 不修仍可引用，只是权重降低 |

口诀（`blockers.py:27`）：**P0 = 不修就无法被引用**。阻塞让 AI「读不到」，
低可引用性只是「不想引」。

---

## 三处消费方

### 1. 渲染层横幅

`render/model.py` + `sections/action-plan.html.j2`。

### 2. `cap_severity_p0`：Critical 降级的唯一出口

位置：`diagnose/blockers.py:343`

```python
cap_severity_p0(severity, kind, blockers=None) -> severity
```

- 只处理 `severity == "Critical"`，其他原样返回
- 传了 `blockers` 时，**只有实际命中的 deterministic blocker key 才放行 Critical**
- 未传 `blockers` 时退回 `_P0_WHITELIST` 常量（向后兼容）

关键在于「实际命中」这四个字。旧实现有两个可绕过点（`blockers.py:353-360` 记录）：

- 标题正则猜 kind：产品页 schema 问题被猜成 `homepage_zero_jsonld`
  → 横幅写「可动工」却出现 P0 卡
- agent 自报 kind：`kind="no_ssr"` 但站点 SSR 正常，同样进 P0

现在 `kind` 只用于「映射到 blocker key」，**最终裁判是 blockers 集合是什么**。

### 3. 执行清单的前置判定

`p0_blocking_note(blockers)`（`blockers.py:313`）：

| blockers 状态 | 文案 |
|---|---|
| 空 / None | 「可进行 GEO 优化，以下内容可慢慢调整。」 |
| 有 deterministic | 「当前官网不可直接 GEO 优化，包含 N 项 P0 级阻塞需前置修复：…」 |
| 只有 inferred | 「可进行 GEO 优化。另有 N 项疑似阻塞待确认：…」 |

---

## `kind` 字段：给 finding 打机器可读标签

### prompt 侧要求

`website_audit_prompt.md:79` 硬性要求：与 `p0_blockers` 对应的 finding **必须**带：

```json
{"title": "...", "severity": "Critical", "kind": "robots_disallow_all", ...}
```

`kind` 原样复制 blocker 的 `key`。

### 渲染侧用法

`render/build.py:365-410`：

```
kind 优先级：blocker key 精确匹配 > agent 自报 kind > 标题正则映射
```

`render/model.py:32-34` 的注释说明：正则只做兜底，历史三次因措辞差异漏配
导致 Critical 被误降。

### 常见错误

| 错误 | 后果 |
|---|---|
| 写了 kind 但 key 拼错 | 回落正则，可能误降级 |
| 该写 kind 没写 | 同上 |
| 不该写 kind 写了（如 sameAs 问题） | 无害，但会被 `cap_severity_p0` 降为 High |
| 写了 `no_ssr` 但站点 SSR 正常 | 降为 High（正确行为） |

---

## 五维评分

权重（`config/*.yaml` → `scoring.weights`）：

| 维度 | 权重 | 数据来源 |
|---|---|---|
| citability | 31.25% | `citability.average_citability_score` |
| content | 25% | citability + fetch（E-E-A-T） |
| technical | 18.75% | fetch + bots |
| schema | 12.5% | schema(sameAs) + fetch |
| platform | 12.5% | bots |

注意：**品牌实体维度已并入 schema**，不再单列。

### 封闭词表（写错一个字母就在报告里显示英文原文）

| 字段 | 允许值 |
|---|---|
| `severity` | `Critical` / `High` / `Medium` / `Low` |
| `confidence` | `Confirmed` / `Likely` / `Hypothesis` |
| `layer` | `domain` / `template` / `editorial` / `page` |
| action `impact` | `High` / `Med` / `Low` ← **`Med` 不是 `Medium`** |
| action `effort` | `minutes` / `hours` / `days` / `weeks` |
| action `owner` | `developer` / `content` / `marketing` |

> `severity` 用 `Medium`，action 的 `impact` 用 `Med`——两者不是同一个字段。

### severity → priority 映射（同一条轴，禁止自创）

| severity | priority |
|---|---|
| Critical（来自 deterministic blocker） | **P0** |
| Critical（非阻塞类 / inferred 疑似） 或 High | **P1** |
| Medium | **P2** |
| Low | **P3** |

**修复成本只影响排序，不影响定级**。Wikidata 词条、媒体报道积累即使影响再大也不进 P0。

---

## 双向对齐（prompt 里最重的约束）

### 正向

清单里每个任务必须能回溯到 `findings` / `quick_wins` / `medium_term` / `strategic`，
禁止自造任务。

### 反向

- `p0_blockers` 里每条 blocker 都必须有一条对应 action（成本高也不许消失）
- `scores.json` 里每一项都必须有对应任务（同根因可合并，不许静默丢弃）

数量预期 8–14 项**服从于反向全覆盖**：若超过 14，按同根因合并到 14 以内，
不允许直接丢弃。

---

## 已知口径不一致处（需要注意）

`website_audit_prompt.md` 与 `prompts/score.md` 是两份独立维护的评分卡：

| 项 | skill 版（17.5K） | score.md（33.3K） |
|---|---|---|
| 输出结构 | 五维 `dimensions` 对象 | 同 |
| `kind` 字段 | **有**（P0 放行对账） | 无 |
| 协议权重表 | 无 | **有** |
| 电商 ItemList 规范 | 无 | **有** |
| 行业自适应外链 | 无 | **有** |
| 时效性五类 | 归入「不属 P0」 | 列为第 5 类「准阻塞」 |

agent 实际执行的是 skill 版。两者已经漂移——见
[04 脆点 · prompt 多源](04-fragility-map.md#prompt-multi-source) 与
[05 优化](05-optimization-roadmap.md#单一真源)。
