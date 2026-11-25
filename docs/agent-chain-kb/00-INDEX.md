# website_analyze Agent 链路知识库

> 面向维护者与接手者的索引。所有结论均以 2026-11-20 的仓库状态与 `results/` 内真实产物为依据，
> 每条事实标注了验证方式与文件位置，可直接复核。

## 这个知识库解决什么问题

`website_analyze/` 是一个**平台导入包**（ZIP），它本身不含执行逻辑——真正跑起来的是
「平台 LLM 读 prompt + 调 4 个 HTTP 工具（`tools/api_server.py`）+ 渲染层（`render/`）」
这条链路。问题往往出在三方交界处：

- prompt 写的要求，工具没有校验；
- 工具返回的数据，prompt 没有消费；
- 渲染层的兜底，把上游的错误静默吞掉。

本知识库按「链路 → 契约 → 口径 → 脆点 → 优化 → 排查 → 证据」组织。

## 文档地图

| 编号 | 文档 | 回答什么问题 |
|---|---|---|
| 01 | [链路总览](01-pipeline-overview.md) | 一条诊断到底跑了哪些步骤？每步的输入输出是什么？ |
| 02 | [数据契约](02-data-contracts.md) | 4 个工具各自的入参/出参形态、模板替换坑、失败语义 |
| 03 | [P0 判定口径](03-p0-and-scoring.md) | 什么算阻塞？三处口径如何同源？`kind` 怎么用？ |
| 04 | [脆点清单](04-fragility-map.md) | 链路上哪里最容易断？按失败代价排序 |
| 05 | [优化路线](05-optimization-roadmap.md) | 按收益/成本排序的改进项，含验收标准 |
| 06 | [排查手册](06-troubleshooting.md) | 症状 → 可能原因 → 定位命令 → 处理 |
| 07 | [证据与复现](07-evidence.md) | 支撑上述结论的实测数据与可复跑命令 |
| 08 | [速查卡](08-quick-reference.md) | 一页纸：关键常量、契约、红线 |

## 三个最需要先知道的事实

1. **`html_result` 是链路的主失败源**。prompt 要求 LLM 把整份 HTML 逐字誊写进最终 JSON，
   当前实测单份 112K–145K 字符（[证据](07-evidence.md#report-size)）。这是必输的搬运。
2. **`report-data` 冗余块已修复**。旧报告（如 hglcn.com）含 100K 字符无人消费的 JSON，
   占体积 64–83%；当前模板已不再输出，报告回落到约 115–145KB。**不要按旧报告下结论。**
3. **`pending_agent_pick` 恒为 `true`**，导致每次诊断都强制多一次 `site_rescan` 往返，
   而服务端其实已做过三层自动采样（[pipeline.py:156-200](../../diagnose/pipeline.py)）。

## 术语

| 词 | 含义 |
|---|---|
| agent | 平台侧编排单元，`website_analyze-agent.json` + `.md` |
| skill | 注入 agent 的 prompt 片段，`skill_resource/*.md` |
| 工具 / tool | agent 可调的 HTTP 端点，`url_config` 指向 `api_server.py` |
| run_id | 服务端暂存键，TTL 2h，承载所有大 JSON |
| blocker | 服务端确定性算出的 P0 阻塞项，唯一裁判 |
| 双脑架构 | 工具做确定性的事，LLM 只做判断与写作 |

## 维护约定

- 改 `website_audit_prompt.md` / `execution_plan_prompt.md` 前，先读 [02 数据契约](02-data-contracts.md)，
  确认字段名与服务端一致。
- 改 `diagnose/blockers.py` 后，跑 `tests/test_blockers.py`、`tests/test_p0_consistency.py`、
  `tests/test_p0_render_consistency.py` 三组。
- 打包 ZIP 前删 `.DS_Store`（当前包内仍带着，见 [04 脆点](04-fragility-map.md#packaging)）。
