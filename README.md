# site-diagnosis-zh — 中文官网 GEO 诊断报告生成器

输入一个 URL，输出一份带图表的单文件**中文** HTML 诊断报告。自包含、零 API key、可在 Claude Code / Codex / 任意能跑 Python 的 agent 环境使用。

## 快速开始

```bash
pip install -r requirements.txt

# 1. 确定性采集
python report.py collect https://example.com --out work/

# 2. agent 评分与中文撰写（由跑本 skill 的 agent 完成，读 prompts/score.md）
#    → 产出 work/scores.json（中文 findings/actions）

# 3. 渲染中文报告
python report.py render work/ --domain example.com --url https://example.com --output 报告.html
```

第 2 步可跳过——没有 `scores.json` 时，渲染器会用确定性数据生成兜底发现，照样产出报告（评分维度会减少）。

## 架构

```
report.py collect  ──► diagnose/  ──► work/*.json   （确定性，任何机器一致）
                                            │
agent 读 score.md ──► work/scores.json        （agent 即 LLM，中文原生撰写）
                                            │
report.py render   ──► render/    ──► 中文 HTML / Markdown / JSON
```

- **diagnose/**：诊断引擎（不产出 HTML）——fetch / robots / bots / citability / schema_check 采集，signals/ 纯事实信号，analyzers/ checks 规则引擎（GEO-XXX-NNN 规则 ID），scoring/ 确定性 baseline + agent override，recommendations/ 行动与 Roadmap 派生，models.py 统一数据模型，cache.py HTTP 缓存，config.py 统一配置
- **render/**：ReportData 组装 + 图表预计算 + Jinja2 / Markdown / JSON 渲染
- **templates/**：中文报告模板（暖纸风格，edgelight 式排版）
- **prompts/**：给 agent 的评分卡（score.md）与汉化契约（translate_zh.md）
- **config/**：default.yaml（权重/阈值/超时集中管理）
- **tests/ + fixtures/**：pytest 回归测试与真实案例 fixtures

## 关键设计

- **零 LLM 依赖**：跑本 skill 的 agent 就是 LLM，不调外部 API。
- **中文原生**：findings/actions 由 agent 评分时直接用中文写，不做"英→中"翻译。
- **确定性采集**：抓页面、解析 robots、爬虫 UA 重放、段落评分——同一 URL 结果一致。
- **降级渲染**：某步失败对应章节显示"未测量"，不产假数据、不阻断。

## 依赖

requests · beautifulsoup4 · lxml · jinja2 · pyyaml（ECharts 走 CDN，可选 --offline 内联）

## 测试

```bash
python3 -m pytest tests/   # 23 个回归测试（JSON-LD 修复 / schema 四态 / 评分 / 渲染冒烟）
```
