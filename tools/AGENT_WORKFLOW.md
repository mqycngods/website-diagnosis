# Agent 工作流编排指南 — 中文官网 GEO 诊断

面向 Coze / 扣子 / Dify / FastGPT 等支持「自定义 HTTP 工具 + 工作流」的平台。

---

## 一、平台导入

### 1. 启动服务

```bash
cd site-diagnosis-zh-2
pip install -r requirements.txt
uvicorn tools.api_server:app --host 0.0.0.0 --port 8000
```

局域网/公网访问时，确认 8000 端口可达。生产建议套一层 nginx + HTTPS。

### 2. 平台侧导入

| 平台 | 导入方式 |
|---|---|
| **Coze / 扣子** | 「插件 → 创建插件 → 基于已有服务创建」→ 填 Base URL `http://your-host:8000` → 「导入」粘贴 `tools/openapi.json` 内容 |
| **Dify** | 「工具 → 自定义 → 创建自定义工具」→ Schema 类型选 OpenAPI → 粘贴 `tools/openapi.json` |
| **FastGPT** | 「高级编排 → 工具调用 → HTTP 请求节点」→ 手动配 4 个请求 |
| **腾讯元器 / 百度千帆** | 类似 Coze，选「OpenAPI 导入」粘贴 schema |

导入后得到 4 个工具：`site_collect` / `site_rescan` / `site_analyze` / `site_render`。

---

## 二、工作流搭建（以 Coze 为例）

### 节点编排

```
[开始] ─ url
   │
   ▼
[工具节点 1: site_collect]
   │  输出变量: collect_result
   ▼
[条件分支] ─ collect_result.articles.pending_agent_pick == true ?
   │ 是                              │ 否
   ▼                                 │
[LLM 节点: 挑 URL]                   │
   │ 输出 agent_picks                │
   ▼                                 │
[工具节点 2: site_rescan]            │
   │ 输出 rescan_result              │
   ▼                                 │
[变量聚合] ◄─────────────────────────┘
   │ articles = rescan ? rescan_result.articles : collect_result.articles
   │ schema   = rescan ? rescan_result.schema   : collect_result.schema
   ▼
[工具节点 3: site_analyze]
   │ 输出 analyze_result
   ▼
[LLM 节点: 写 scores.json] ─ 读 analyze_result + collect_result
   │ 输出 scores（中文 findings/actions）
   ▼
[工具节点 4: site_render]
   │ 输出 HTML 字符串
   ▼
[结束] ─ 返回 HTML（或上传 OSS 返链接）
```

### 关键变量传递规则

| 工具入参 | 取值 |
|---|---|
| `site_analyze.url` | `collect_result.url` |
| `site_analyze.fetch_page` | `collect_result.fetch` |
| `site_analyze.articles` | 条件：`rescan_result ? rescan_result.articles : collect_result.articles` |
| `site_analyze.pages_audit` | `collect_result.pages` |
| `site_analyze.schema` | 条件：同上 |
| `site_analyze.citability` | `collect_result.citability` |
| `site_analyze.bots` | `collect_result.bots` |
| `site_analyze.technical` | `collect_result.technical` |
| `site_render.*` | 同上 + `signals_summary` / `issues` / `brand` 来自 `analyze_result`，`scores` 来自 LLM 节点 |

---

## 三、LLM 节点 Prompt 模板

### LLM 节点 A（条件分支）：挑 URL

```
你是 URL 分类器。下面是某官网首页抓到的所有内部链接：

{{collect_result.articles.internal_links}}

任务：挑出最具代表性的 5 篇「文章/博客/资讯」URL 与 3 个「产品/服务详情页」URL。
规则：
- 文章页特征：包含 /blog/ /news/ /article/ /insights/ /post/ 或日期路径
- 产品页特征：包含 /product/ /products/ /solution/ /service/
- 避开：首页、about、contact、tag、category、author、分页、登录注册

输出严格 JSON（不要任何其他文字）：
{
  "articles": ["url1", "url2", ...],
  "products": ["url1", "url2", ...]
}
```

### LLM 节点 B：写中文 scores.json

读 `prompts/score.md` 完整评分卡。核心约定：

```
你是中文 GEO 诊断专家。基于以下确定性数据，为站点写中文评分与优化建议。

【站点】{{collect_result.url}}

【五维 baseline】
{{analyze_result.baseline_scores}}

【issues 规则引擎命中】
{{analyze_result.issues}}

【信号摘要】
{{analyze_result.signals_summary}}

【可引用性段落评分】
{{collect_result.citability}}

【AI 爬虫实测】
{{collect_result.bots}}

任务：输出严格 JSON，结构如下（所有文本必须中文）：
{
  "overall_score": 0-100 整数,
  "overall_label": "优秀|良好|中等|偏弱|堪忧",
  "dimensions": [
    {"name": "可引用性", "score": ..., "findings": ["...","..."], "actions": ["...","..."]},
    {"name": "AI 可达性", ...},
    {"name": "技术基础", ...},
    {"name": "结构化数据", ...},
    {"name": "内容质量", ...}
  ],
  "top_priorities": [
    {"title": "...", "impact": "高|中|低", "effort": "分钟|数小时|数天|数周", "owner": "开发|内容|市场"}
  ],
  "paragraph_hints": [
    {"heading": "原文小标题", "hint": "改写建议"}
  ]
}

要求：
1. findings 直接陈述事实+影响，不写"我们发现/建议"等冗余主语
2. actions 必须可执行，带具体位置（首页/文章模板/产品页模板）
3. 不超 5 个 dimension、5 条 top_priorities、10 条 paragraph_hints
4. 所有 score 与 baseline 误差不超过 ±15，除非你有强证据
```

---

## 四、平台不支持的兜底方案

如果平台**只支持单变量入参（`{{url}}`），不支持工具间 JSON 传递**——那 4 端点用不了。这种情况告诉我，我加一个 `POST /diagnose` 一键端点：

```
POST /diagnose  {"url": "..."}  →  直接返回 HTML 报告
```

内部串行跑完 collect→analyze→render，**LLM 不中途介入**（scores 走兜底逻辑）。报告质量略降，但任何平台都能用。

---

## 五、故障排查

| 症状 | 排查 |
|---|---|
| 平台提示「无法访问服务」 | 确认 8000 端口开放；`curl http://your-host:8000/health` 通 |
| `/collect` 返回 `fetch.error` | 目标站封爬虫/超时，看 `error` 字段信息；服务本身没问题 |
| `/render` 422 | 检查 `schema` 字段是不是 `schema_`（应该是 `schema`，FastAPI alias 已处理） |
| 报告章节显示「未测量」 | 对应字段没传或传了 `{"error": "..."}`；看工作流的变量聚合 |
| ECharts 不渲染 | 平台沙箱禁外网时把 `offline` 设为 `true` |
