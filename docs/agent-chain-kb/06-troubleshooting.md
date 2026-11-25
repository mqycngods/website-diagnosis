# 06 · 排查手册

症状驱动。每条：**症状 → 先看什么 → 可能原因 → 定位命令 → 处理**。

---

## A. agent 最终被判失败 / 缺字段

### A1. 报「缺少 html_url / md_url / html_result」

**先看**：`site_render` 的响应原文。

| 观察 | 结论 |
|---|---|
| `success: false` 且无 `report_url` | render 抛异常，走兜底分支（`api_server.py:641`） |
| `success: true` 但最终 JSON 缺字段 | LLM 输出被截断（`html_result` 太长） |
| 响应是纯文本/HTML 错误页 | 网关层拦截（见 A4） |

**定位**：

```bash
# 看服务端日志里的渲染失败原因
grep -n "\[render\]" logs/*.log 2>/dev/null | tail -20
```

**处理**：
- render 异常 → 按 `error` 字段定位（见 B 组）
- 输出截断 → 见 [05 #1](05-optimization-roadmap.md#1-html-result)

---

### A2. 报「四个字段拆成了多个对象」

**原因**：LLM 输出了 `[{"result":...},{"html_url":...}]`。

**为什么**：`website_analyze-agent.md:54` 已明确禁止，但模型在长输出时容易走样。

**处理**：检查是否 `html_result` 过大导致模型「分段输出」。根因同 A1。

---

### A3. `success: true` 但报告内容明显残缺

**先看**：响应里的 `scores_parse_error`。

| 值 | 含义 |
|---|---|
| `null` | scores 正常 |
| `"scores 不是 JSON 对象"` | 传了数组/字符串 |
| `"scores 无法解析为 JSON..."` | 语法错误，走了兜底 |

**后果**：非 null 时报告用的是确定性兜底 findings，LLM 写的评分与发现**全部丢失**。

**处理**：见 [05 #7](05-optimization-roadmap.md) 与
[04 #7](04-fragility-map.md#scores-parse-error)。

---

### A4. render 返回非 200 / HTML 错误页

**背景**：Render 平台的 WAF 会误拦渲染请求。历史修复记录：

```
f153ae1 fix(render): 修复 Render WAF 误拦渲染请求
ef15489 fix(render): 渲染节点默认先发 Base64 规避 WAF
a8a41fe fix(render): 渲染请求改用明文并取消 WAF 重试
b9beb7c fix(diagnose,render): 降级疑似阻塞并将渲染请求改用 text/plain
```

**现状**：`agent.json:216` 用 `Content-Type: text/plain`。

**处理**：若再次出现，按 `f153ae1`~`b9beb7c` 的思路调整
（Base64 / 明文 / 重试策略的组合）。

---

## B. 报告内容不对

### B1. 报告横幅说「可动工」，但清单里有 P0

**根因**：`cap_severity_p0` 未生效，或 `kind` 未匹配。

**定位**：

```bash
# 看这次运行的 p0_blockers 与 scores 里的 findings.kind
python3 -I - <<'EOF'
import json
d = json.load(open('results/<dir>/report-data.json', encoding='utf-8'))
print("p0_blockers:", d.get('p0_blockers'))
for f in d.get('findings', []):
    print(f.get('severity'), '|', f.get('kind'), '|', f.get('title'))
EOF
```

**处理**：
- `p0_blockers` 为空但 findings 有 Critical → `cap_severity_p0` 会降为 High（正确）
- `p0_blockers` 有项但 findings 无对应 `kind` → 检查 prompt 是否漏了 kind 要求

---

### B2. 报告说「全站无日期」但页面明明有日期

**根因**：采样到了列表页，而非详情页。

**背景**：列表页没有 Article schema 与发布日期（`website_analyze-agent.md:17`）。

**定位**：

```bash
python3 -I - <<'EOF'
import json
d = json.load(open('results/<dir>/report-data.json', encoding='utf-8'))
arts = d.get('articles', {})
print("sampled:", arts.get('sampled'))
print("pages:", [p.get('url') for p in arts.get('pages', [])])
for p in arts.get('pages', []):
    print(p.get('url'), '→', p.get('freshness'))
EOF
```

**处理**：
- 若 `pages` 里是 `/blog`、`/news` 这类栏目页 → 采样逻辑问题，见
  `diagnose/sampling.py`
- 若是详情页但 `freshness` 为空 → 站点确实没有机器可读日期，属正常判定

---

### B3. 报告评分与 baseline 差很远

**说明**：`baseline_scores`（服务端确定性）与 `scores`（LLM 写）本就是两套。
报告展示的是 LLM 的 `scores`，baseline 只作参考输入。

**若差异极大**：检查 LLM 是否拿到了完整的 `p0_blockers` 与 `issues`。
见 [02 契约 · analyze 出参](02-data-contracts.md#analyze-endpoint)。

---

### B4. 报告里有「未测量」但实际采到了

**根因**：`_meta.partial` 为 true（部分子任务超时），但相关字段被写成 `error`。

**定位**：

```bash
python3 -I - <<'EOF'
import json
d = json.load(open('results/<dir>/report-data.json', encoding='utf-8'))
for k in ('bots','citability','articles','pages','schema','technical'):
    v = d.get(k)
    if isinstance(v, dict) and v.get('error'):
        print(k, '→', v.get('error'))
EOF
```

**处理**：调大 `pipeline.fetch_all` 的 `soft_timeout`（当前 `collect` 传 60s）。

---

## C. 链路结构性问题

### C1. 每次诊断都触发 rescan，耗时偏长

**这是当前设计行为**（`pending_agent_pick` 恒为 true）。
见 [04 #4](04-fragility-map.md#pending-agent-pick)。

**临时缓解**：无（除非改代码）。

---

### C2. 多副本部署时 run_id 找不到

**症状**：`404 run_id 不存在或已过期（2 小时有效）`。

**根因**：`STORE_BACKEND=memory` 时 run_id 存在进程内存，多副本会跨请求丢失。

**处理**：

```bash
export STORE_BACKEND=redis
export REDIS_URL=redis://...
```

**验证**：

```bash
curl -s http://host/health
# 检查返回里的 store 后端
```

---

### C3. render 提示「缺少 url」400

**根因**：平台模板的 `{{url}}` 变量为空。

**定位**：`api_server.py:608-615` 的 400 detail 里已经带了
`run_id`、`body_keys`、`body.url`、`stored.url`——直接看这句。

**处理**：检查平台工具节点的参数映射，确认 `url` 字段绑定正确。

---

## D. prompt 相关问题

### D1. 改了 `score.md` 但线上行为没变

**原因**：agent 执行的是 `website_analyze/skill_resource/website_audit_prompt.md`，
不是 `prompts/score.md`。

见 [04 #2](04-fragility-map.md#prompt-multi-source)。

**处理**：改 skill 文件，并重新导入 ZIP。

---

### D2. 报告里出现英文 severity（如直接显示 `Critical`）

**根因**：封闭词表写错，渲染层映射失败。

**定位**：检查 `scores.json` 里 severity/impact/effort/owner 的值是否在词表内。
特别注意 `impact` 用 `Med` 不是 `Medium`。

---

## 快速健康检查（一条命令）

```bash
python3 -I - <<'EOF'
import json, glob, os
rows = []
for p in sorted(glob.glob('results/*/report-data.json'), key=os.path.getmtime)[-10:]:
    d = json.load(open(p, encoding='utf-8'))
    html = glob.glob(os.path.dirname(p) + '/*-geo-report.html')
    size = os.path.getsize(html[0]) if html else 0
    rows.append({
        'dir': os.path.basename(os.path.dirname(p)),
        'html_kb': size // 1024,
        'blockers': len(d.get('p0_blockers') or []) if isinstance(d.get('p0_blockers'), list) else '-',
        'findings': len(d.get('findings') or []),
        'score': d.get('geo_score'),
    })
for r in rows:
    print(r)
EOF
```

期望：`html_kb` 在 100–150 区间，`findings` 在 6–10，
`blockers` 与报告横幅一致。
