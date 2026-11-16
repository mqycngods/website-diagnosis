# Agent 导入包说明

## 一、压缩包格式

本文件夹必须整体打包为 ZIP 文件。解压后根目录包含 `README.md`、一个以 `agent.json` 结尾的配置文件、一个以 `agent.md` 结尾的系统 Prompt 文件，以及可选的 `skill_resource/` 和 `tool_resource/` 目录。两个 Agent 文件必须使用相同前缀，例如 `customer-agent.json` 和 `customer-agent.md`。文本文件统一使用 UTF-8 编码。

## 二、文件说明

- `*agent.json`：文件名以 `agent.json` 结尾，保存 Agent 元数据、输入输出 Schema、关联技能、关联工具和资源引用。
- `*agent.md`：文件名以 `agent.md` 结尾，保存 Agent 系统 Prompt，由 `agent.system_prompt_ref` 引用；它必须与配置文件使用相同前缀。
- `skill_resource/`：技能 Prompt 文件目录，文件名没有固定要求，具体文件由 `skill_prompt_ref` 引用。
- `tool_resource/`：Python 工具代码目录，文件名没有固定要求，具体文件由 `code_content_ref` 引用。

系统 Prompt、技能 Prompt 和工具代码均为独立文件。请勿将这些内容重新写回 `*agent.json`，也不要删除对应的引用字段。

## 三、字段说明

### 3.1 顶层字段

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `project_id` | integer | Agent 所属项目 ID。导入时必须与当前项目一致。 |
| `agent` | object | Agent 配置对象。 |
| `skills` | array | 技能定义数组。 |
| `tools` | array | 工具定义数组。 |

### 3.2 `agent` 字段

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `agent_key` | string | Agent 英文唯一标识，项目内用于匹配和覆盖。 |
| `agent_name` | string | Agent 展示名称。 |
| `visibility` | integer | 可见性：`0` 私有，`1` 公开。 |
| `agent_type` | integer | Agent 类型：`0` 任务型，`1` 对话型。 |
| `agent_type_label` | string | Agent 类型展示文本，例如“任务型”“对话型”。导入时主要以 `agent_type` 为准。 |
| `model_key` | string/null | 使用的模型英文标识；为空表示未指定。 |
| `system_prompt_ref` | string | 系统 Prompt 文件引用路径，必须指向根目录的 `*agent.md` 文件。 |
| `input_schema` | array | Agent 输入参数结构。 |
| `output_schema` | array | Agent 输出参数结构。 |
| `temperature` | number | 模型采样温度，通常为 `0` 到 `1`。 |
| `max_retries` | integer | Agent 执行失败时的最大重试次数。 |
| `timeout_seconds` | integer | Agent 执行超时时间，单位为秒。 |
| `skill_refs` | string[] | 关联技能的 `skill_name_en` 列表。 |
| `tool_refs` | string[] | 关联工具的 `tool_name_en` 列表。 |
| `options` | object/null | Agent 扩展配置。值可以是字符串或字符串数组。 |
| `callbacks` | array/null | Agent 回调配置数组；没有回调时为 `null`。 |

### 3.3 `input_schema` / `output_schema` 字段

Schema 是参数对象数组，每个参数支持以下字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `type` | string | 参数类型，例如 `string`、`number`、`boolean`、`object`、`array`。 |
| `field` | string | 参数字段名。 |
| `required` | boolean | 是否必填。 |
| `description` | string | 参数说明。 |
| `children` | array | `object` 或 `array` 类型的子字段，结构与当前参数相同。 |

### 3.4 `skills` 中的字段

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `skill_name` | string | 技能展示名称。 |
| `skill_name_en` | string | 技能英文唯一标识，必须与 `skill_refs` 中的值对应。 |
| `description` | string | 技能说明。 |
| `skill_prompt_ref` | string | 技能 Prompt 文件引用路径，指向 `skill_resource/` 下的实际文件。 |

### 3.5 `tools` 中的字段

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `tool_name` | string | 工具展示名称。 |
| `tool_name_en` | string | 工具英文唯一标识，必须与 `tool_refs` 中的值对应。 |
| `description` | string | 工具说明。 |
| `tool_type` | integer | 工具类型：`0` Python 工具，`1` URL 工具。 |
| `input_params` | array | 工具入参定义数组。 |
| `code_content_ref` | string | Python 工具代码文件引用路径，仅 `tool_type=0` 时使用。 |
| `url_config` | object | URL 工具请求配置，仅 `tool_type=1` 时使用。 |

### 3.6 `tools[].input_params` 字段

工具入参字段与 Schema 基本一致，另外支持默认值：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `type` | string | 入参类型。 |
| `field` | string | 入参字段名。 |
| `default` | any | 默认值，可以是字符串、数字、布尔值或其他 JSON 值。 |
| `required` | boolean | 是否必填。 |
| `description` | string | 入参说明。 |
| `children` | array | 对象或数组入参的子字段。 |

### 3.7 `tools[].url_config` 字段

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `url` | string | 请求地址。 |
| `method` | string | HTTP 方法，例如 `GET`、`POST`。 |
| `params` | object | URL 查询参数。 |
| `headers` | object | HTTP 请求头。 |
| `body` | string/object | 请求体模板，可以是字符串或 JSON 对象。 |
| `body_type` | string | 请求体类型，例如 `json`、`form-data`、`raw`。 |
| `auth_type` | string | 认证类型，例如 `none`、`bearer`、`knowledge-aes`。 |
| `auth_config` | object | 认证参数。敏感字段导出时会被过滤。 |
| `timeout` | number | HTTP 请求超时时间，单位为秒。 |

### 3.8 `agent.callbacks` 字段

每个回调对象支持以下字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `name` | string | 回调名称。 |
| `enabled` | boolean | 是否启用。 |
| `trigger_on` | string[] | 触发事件，例如 `completed`、`failed`。 |
| `tool_id` | integer/null | 引用已有 URL 工具的 ID；与 `url_config` 二选一。 |
| `url_config` | object/null | 回调使用的内联 URL 配置；与 `tool_id` 二选一。 |
| `payload_mapping` | object/null | 回调请求字段到模板表达式的映射。 |
| `retry` | integer | 回调发送失败时的重试次数。 |

## 四、引用规则

所有引用都是相对于 ZIP 根目录的路径，必须以 `./` 开头。禁止使用 `../`、绝对路径、网络地址或其他 ZIP 外部路径。Python 工具使用 `code_content_ref`，URL 工具使用 `url_config`。

## 五、重新打包

人工编辑后，请保持两个 Agent 文件的相对路径和相同前缀，并确保所有引用路径仍能定位到对应资源，再将 ZIP 根目录中的内容重新压缩。导入时会校验引用文件是否存在、技能和工具英文标识是否重复，以及 `project_id` 是否匹配当前项目。
