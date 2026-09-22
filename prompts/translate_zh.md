# 汉化契约 — 本 skill 不做"英文→中文"翻译

本项目的中文报告**不靠事后翻译**。中文在【评分撰写】阶段由 agent 直接产出（见 score.md）。
本文件说明为什么、以及边界在哪。

## 原则：原生中文，而非翻译

- Agent 在写 `scores.json` 的 findings/actions/description 时，**直接用中文写**。
- 不存在"先写英文再翻成中文"的环节——那样既慢又引入翻译误差。
- 因此渲染管线（render/）里**没有任何翻译映射表**。

## 边界：什么已经是中文 / 什么保持英文

| 内容 | 语言 | 由谁处理 |
|---|---|---|
| findings 的 title/evidence/impact/fix | 中文 | agent 评分时直接写 |
| actions 的 action | 中文 | agent 评分时直接写 |
| description / impact_estimate | 中文 | agent 评分时直接写 |
| severity / confidence / layer | **英文机器值** | render/build.py 填 `*_zh` 伴生（固定小集合） |
| verdict / bot_class | **英文机器值** | render/build.py 填 `*_zh` 伴生 |
| 采集 JSON 的引文（段落原文、URL、爬虫名、HTTP 状态） | 原文 | 不处理 —— 那是证据，必须保真 |

## 为什么枚举值保留英文

`severity`、bot_class` 等字段同时承担两个角色：
1. **机器判断**：CSS 类名（`sev-Critical`）、逻辑分支（`verdict == "OPEN"`）依赖英文原值。
2. **中文显示**：模板渲染 `severity_zh`。

让 agent 翻译它们会破坏样式与逻辑。所以它们保持英文机器值，由 `render/build.py`
用一组**固定、极小的对照**填 `*_zh` 中文伴生字段——这组对照只覆盖约 20 个永不变拼写的
枚举词，不属于"自由文本翻译"，是稳定的契约常量。

## 如果你在扩展维度

新增维度/字段时遵守：
- 描述性文字 → 让 agent 用中文写（改 score.md 的评分卡）。
- 新的枚举机器值 → 在 render/build.py 的对照里加一行 `*_zh`，并在 model.py 加对应伴生字段。
- 永远不要在本项目里引入"自由文本英文→中文"的映射表。
