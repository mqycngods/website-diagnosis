# 07 · 证据与复现

本页记录支撑知识库结论的实测数据与可复跑命令。
所有数字来自本仓库 `results/` 目录的真实产物（2026-11-20 采集）。

---

<a id="report-size"></a>

## 报告体积

### 命令

```bash
# 全部报告按体积排序
find results -name "*-geo-report.html" -exec ls -la {} \; \
  | awk '{printf "%9d  %s\n", $5, $9}' | sort -rn | head -12
```

### 实测结果

| 报告 | 字符数 | 代际 |
|---|---|---|
| hglcn.com-20261105-204008 | **1,238,984** | 旧（内联 ECharts + report-data） |
| housebell.com-20261102-175111-divfix | 503,559 | 旧 |
| housebell.com-20261102-171914 | 487,625 | 旧 |
| blog.edgelight.com-20260920-165632 | 328,493 | 旧 |
| pockella.com-20261119-143058 | 144,811 | **新** |
| dianclaw.cn-20261118-215156 | 116,251 | **新** |
| creativehit.ai-20261118-173208 | 112,467 | **新** |

**结论**：
- 旧模板单份最大 1.24M 字符，其中内联 ECharts 库 1,024,031 字符（83%）
- 新模板（2026-11-18 起）体积降到 112K–145K，约为旧模板的 1/10
- 当前 `html_result` 需誊写 11 万–14.5 万字符

### 旧报告的内联 ECharts

```bash
python3 -I - <<'EOF'
import re
p = 'results/hglcn.com-20261105-204008/hglcn.com-geo-report.html'
s = open(p, encoding='utf-8', errors='replace').read()
print('total', len(s))
for m in re.finditer(r'<script[^>]*>(.*?)</script>', s, re.S):
    print(len(m.group(1)), m.group(0)[:60].replace('\n', ' '))
EOF
```

输出：

```
1024031  <script> /* Licensed to the Apache Software Foundation (ASF)...
   1267  <script> (function(){ var root = document.currentScript...
   1075  <script> (function () { var tip =...
    680  <script> /* 行动方案 · tab 切换...
   1029  <script> /* 附录跳转：平滑滚动...
  99740  <script type="application/json" id="report-data">{...
   5302  <script type="application/json" id="chart-data">{"colors":...
   4686  <script> (function () { if (typeof echarts === "undefined")...
```

---

## `report-data` 冗余验证

### 命令

```bash
for f in results/*/*-geo-report.html; do
  n=$(grep -o 'getElementById("report-data"' "$f" | wc -l | tr -d ' ')
  echo "$n  $(basename $(dirname $f))/$(basename $f)"
done | sort -rn | head
```

### 结果

**全部样本命中数为 0** —— 没有任何 JS 读取 `report-data` 块。

该块在旧模板中最大 99,740 字符（hglcn.com），占该报告 8%；
在 housebell.com-20261102-171914 中占 292,327 / 455,361 = **64%**。

### 当前模板状态

```bash
grep -c "report_json" render/html.py render/inline_templates.py
# render/html.py:1        ← 传入模板上下文
# render/inline_templates.py:0  ← 模板里已不再使用
```

**结论**：当前模板已不再输出 `report-data` 块，问题已修复。历史产物仅作证据。

---

## 两代模板并存验证

```bash
# 含 window.__CHART_DATA__ 的报告（新代）
grep -rl "__CHART_DATA__" results/*/*.html | head -6
```

结果：

```
results/dianclaw.cn-20261118-215156/dianclaw.cn-geo-report.html
results/creativehit.ai-20261118-163922/creativehit.ai-geo-report.html
results/creativehit.ai-20261118-173208/creativehit.ai-geo-report.html
results/pockella.com-20261119-143058/pockella.com-geo-report.html
results/pockella.com-en-20261119-150758/pockella.com-geo-report.html
```

对比同一份旧报告：

```bash
f=results/hglcn.com-20261105-204008/hglcn.com-geo-report.html
for pat in "__CHART_DATA__" 'id="chart-data"' 'id="report-data"'; do
  printf "%-24s %s\n" "$pat" "$(grep -o "$pat" $f | wc -l | tr -d ' ')"
done
```

```
__CHART_DATA__           0
id="chart-data"          1
id="report-data"         1
```

**结论**：旧模板用 `chart-data` + `report-data` 两个 JSON 块；
新模板用 `window.__CHART_DATA__` JS 变量，无 `report-data`。

> 注意：`website_analyze-agent.md:58` 的说明与新模板一致
> （「模板已把图表数据内联到 `window.__CHART_DATA__`」），
> 但与 `results/` 里的旧产物不符。新代产物证明该说明是对的。

---

## `/collect` 载荷构成

### 命令

```bash
python3 -I - <<'EOF'
import json
d = json.load(open('results/housebell.com-20261102-171914/report-data.json', encoding='utf-8'))
for k, v in d['fetch'].items():
    print(f"{k:22s} {len(json.dumps(v, ensure_ascii=False))}")
EOF
```

### 结果

```
url                    28
status_code            3
title                  81
description            200
canonical              27
meta_tags              1061
h1_tags                56
heading_structure      287
word_count             4
text_content           57164   ← 最大
internal_links         2186
external_links         247
images                 37167   ← 次大
structured_data        1972
security_headers       178
freshness              285
has_ssr_content        4
challenge_detected     5
errors                 2
```

**结论**：`text_content`（57K）+ `images`（37K）占 fetch 载荷的 94%，
而后续步骤只用 `word_count`/`has_ssr_content`/`freshness`/`structured_data`。

---

## P0 判定规则测试覆盖

```bash
python3 -m pytest tests/test_blockers.py tests/test_p0_consistency.py \
                 tests/test_p0_render_consistency.py tests/test_robots_disallow_blocker.py -q
```

覆盖的场景（来自测试文件名与 fixture）：

| 测试文件 | 覆盖 |
|---|---|
| `test_blockers.py` | 每类规则命中 / 边界 / 抑制三组 |
| `test_p0_consistency.py` | p0_blockers 与 baseline 一致性 |
| `test_p0_render_consistency.py` | 横幅 / 清单 / 评分三处口径 |
| `test_robots_disallow_blocker.py` | robots 全禁的声明层判定 |

**改 `blockers.py` 后必须全跑。**

---

## `pending_agent_pick` 恒为 true

### 代码

`diagnose/pipeline.py:204`：

```python
articles = {"sampled": article_urls, "sampled_products": product_urls,
            "pages": [], "product_pages": [],
            "pending_agent_pick": True, "internal_links": internal_links}
```

### 文档佐证

`diagnose/pipeline.py:147`：

> `articles` 恒带 `pending_agent_pick: true`（Phase 1.5 每次诊断都触发）

### 服务端已有的三层采样（同文件）

```
pick_article_urls(internal_links, limit=5)           # 模式匹配
_expand_list_pages(list_pages)                        # 列表页展开
sitemap.discover_sitemaps(url) → pick_article_urls   # sitemap 兜底
```

**结论**：服务端能自动挑的已经挑了，agent 的 rescan 主要价值在于
「识别列表页 vs 详情页」，这已被 `sampling.pick_article_urls` 的模式覆盖。

---

## 打包体积

```bash
unzip -l website_analyze.zip
```

```
    9186  website_analyze/website_analyze-agent.md
     163  __MACOSX/website_analyze/._website_analyze-agent.md
    6148  website_analyze/.DS_Store          ← 22% 体积
     120  __MACOSX/website_analyze/._.DS_Store
    ...
   27447  总计
```

**结论**：`.DS_Store` + `__MACOSX` 资源叉占包体约 22%，应清理。

---

## 密钥明文

```bash
grep -n "X-API-Key" website_analyze/website_analyze-agent.json
```

```
95:          "X-API-Key": "stpTeS**********",   // 4 处，值相同
136:
169:
215:
```

**结论**：Key 明文存在于分发包中。

---

## 复现环境

- 仓库：`/Users/maqinyao/Desktop/site-diagnosis-zh-2`
- 分支：`main`
- 采集时间：2026-11-20
- 依赖：`requirements.txt`（本地跑诊断需 `bs4` 等，`python -I` 直接导入
  `diagnose.fetch` 会缺依赖——本页所有命令只读文件，不导入项目模块）

### 只读命令集（无需安装依赖）

```bash
# 1. 报告体积分布
find results -name "*-geo-report.html" -exec ls -la {} \; \
  | awk '{print $5, $9}' | sort -rn | head -10

# 2. report-data 是否被消费
grep -c 'report-data' results/*/*-geo-report.html | awk -F: '$2>0' | head

# 3. 新旧模板判别
grep -l "__CHART_DATA__" results/*/*.html | head

# 4. 打包卫生
unzip -l website_analyze.zip | grep -c "DS_Store\|__MACOSX"

# 5. 密钥扫描
grep -rn "X-API-Key" website_analyze/*.json
```
