# GEO 官网诊断（中文）— Dify 工作流

输入一个 URL，输出：GEO 评分 + 单文件 HTML 报告 + Markdown 报告 + GEO 优化执行清单（Markdown）。
无文件系统会话：采集数据由服务端按 `run_id` 内存暂存（TTL 2h），报告内容直接从 `diagnosis_render` 端点以字符串返回。

## 快速开始

### 1. 部署服务端端点

`server_endpoints.py` 是参考实现（Flask Blueprint），依赖本仓库的 `diagnose/` 与 `render/` 包。

```bash
# 独立跑（在 site-diagnosis-zh-2 仓库根目录）
export GEO_ACCESS_TOKEN=<真实令牌>
pip install flask -r requirements.txt
python dify-site-diagnosis-zh/server_endpoints.py   # 默认 9100 端口
```

或把 `bp` 注册进现有 geo-server。**内存暂存要求单 worker**（gunicorn `--workers 1`）；多 worker 部署需把 `_STORE` 换成 Redis。

### 2. 导入 DSL

1. Dify → 工作室 → **导入 DSL 文件** → 选 `workflow.dsl.yaml`
2. 配置环境变量：

| 变量名 | 值 |
|---|---|
| `biz_api_base` | 诊断服务端地址，如 `http://123.57.162.4:9100` |
| `geo_api_token` | 与服务端 `GEO_ACCESS_TOKEN` 一致 |

3. 三个 LLM 节点（4 挑页 / 7 评分 / 10 执行计划）手动选模型——评分与执行计划建议用遵循复杂规则能力强的模型（Claude / GPT-4o 级）。

### 3. 运行

`test-input.json` 是输入示例。一次完整运行约 2-4 分钟（采集大头在 12 个 AI 爬虫 UA 实测与抽样页抓取）。

## 节点拓扑

```
开始 (url 必填 / brand_name 可选)
  ↓
0. 读取配置（透传 env，生成 run_id / domain / 归一化 url）
  ↓
1. 核心抓取      diagnosis_fetch_core   → fetch_summary / robots / technical
  ↓
2. 爬虫与引用    diagnosis_probe_bots   → bots / citability_summary
  ↓
3. 页面扫描      diagnosis_scan_pages   → internal_links / 抽样 / pages / schema
  ↓
4. LLM 挑页（structured_output：articles + products）
  ↓
5. 补抓重建      diagnosis_rescan_pages → 最终 schema / 抽样清单
  ↓
6. 综合分析      diagnosis_analyze      → brand / issues / signals_summary / baseline_scores
  ↓
7. LLM 评分（structured_output：六维分 + findings + 行动，全中文）
  ↓
8. 校验修正（本地解析 *_json 字符串，修枚举报错，回退 baseline）
  ↓
9. 保存评分      diagnosis_save_scores  → scores 写入 run_id 暂存
  ↓
10. LLM 执行计划（structured_output：plan_markdown）
  ↓
11. 渲染报告     diagnosis_render       → geo_score / report_html / report_markdown / 下载链接
  ↓
输出（geo_score / label_zh / report_html / report_markdown / execution_plan_md / download_url_* / run_id）
```

## 输出字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `geo_score` | Number | GEO 综合分（0-100） |
| `label_zh` | String | 中文评级（偏弱/中等/良好…） |
| `report_html` | String | 单文件中文 HTML 报告（内联 CSS + ECharts） |
| `report_markdown` | String | Markdown 版报告 |
| `execution_plan_md` | String | GEO 优化执行清单（8-14 项任务，含验收清单） |
| `download_url_html` / `download_url_md` | String | 浏览器直接下载链接（2h 有效，query token 鉴权） |
| `run_id` | String | 本次诊断 ID（排查问题用） |

## 与 SKILL.md 链路的对应关系

| SKILL.md Phase | 工作流节点 |
|---|---|
| Phase 0 建目录 | 取消——改为节点 0 生成内存 `run_id` |
| Phase 1 collect | 节点 1/2/3（`diagnose/pipeline.py` 的 `fetch_core` / `probe_bots` / `scan_pages` 纯函数） |
| Phase 1.5 agent 挑页 | 节点 4（LLM）+ 节点 5（`rescan_pages`） |
| Phase 2 agent 评分 | 节点 7（LLM，system prompt 是 `prompts/score.md` 的压缩版）+ 节点 8/9 |
| Phase 2.5 执行计划 | 节点 10（LLM，system prompt 是 SKILL.md Phase 2.5 的压缩版） |
| Phase 3 render | 节点 11（`build_report_from_dict` 纯内存渲染，不落地 results/ 目录） |

## 关键设计

- **深度规避**：节点 7 的 findings/dimensions 等以 JSON **字符串**输出（`findings_json` 等），节点 8 本地解析修正——绕开 Dify structured_output 的嵌套深度与 30 元素限制（同 Agent A v2 节点 13 的 `config_json` 思路）
- **payload 控制**：首页正文不进 Dify 变量；节点间只传摘要（fetch_summary / citability_summary / signals_summary），全量数据留服务端内存
- **失败降级**：采集类端点单步失败写 `{"error": ...}` 不阻断，渲染层对应章节显示「未测量」
- **重试**：所有 HTTP 节点 3 次指数退避 + Dify retry_config

## 故障排查

- **节点报 Connection refused / 超时**：检查 `biz_api_base` 是否可达、服务端进程是否在跑
- **401**：`geo_api_token` 与服务端 `GEO_ACCESS_TOKEN` 不一致
- **渲染出来「未测量」大片**：看节点 1/2/3 的 `error` 字段——对应采集步骤失败但流程未阻断
- **findings 为空或维度分数全是 baseline**：节点 7 的 LLM 输出被节点 8 兜底修正了，看节点 8 的 `fixes_applied`

## 文件清单

- `workflow.dsl.yaml` — 完整工作流 DSL（14 节点 / 13 边，由脚本生成）
- `generate_dsl.py` — DSL 生成脚本（改节点代码/prompt 改这里，重新运行即可）
- `server_endpoints.py` — 服务端 7 个端点 + 1 个下载端点的参考实现
- `test-input.json` — 测试输入
