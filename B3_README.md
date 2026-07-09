# B3 Tools Schema 与 Tool Calls 执行层

B3 是本地 Agent 项目中的工具调用执行层，入口文件为：

```text
code/b3_tool_layer.py
```

它负责把项目中的 Skill 工具转换成大模型可理解的 `tools_schema`，并在模型生成 `tool_calls` 后完成参数校验、工具执行、结果封装和日志记录。

B3 本身不调用大模型。它处在 B4 大模型决策层和 B2 Skill 工具层之间，是连接“模型想调用工具”和“系统真正执行工具”的中间层。

## 1. 模块职责

B3 主要完成两类工作：

1. 生成工具说明 schema。

   从 `configs/tools.yaml` 读取工具定义，生成 OpenAI function calling 风格的 `tools_schema.json`，供 B4 或真实大模型参考。

2. 执行 tool calls。

   接收 AIMessage 中的 `tool_calls`，校验工具名和参数，动态加载对应 Skill 函数，执行后生成标准 ToolMessage。

整体关系如下：

```text
B1 Runtime
  -> 调用 B3 生成 tools_schema
  -> 将 tools_schema 交给 B4

B4 Local Agent LLM
  -> 根据 tools_schema 生成 AIMessage.tool_calls

B1 Runtime
  -> 将 tool_calls 交给 B3 执行

B3 Tool Layer
  -> 校验参数
  -> 调用 Skill
  -> 返回 ToolMessage
```

## 2. 支持的工具

当前 `basic_tools` 工具集包含 11 个工具：

```text
calculator
file_reader
local_file_search
table_analyzer
format_converter
code_executor
text_summarizer
search_read_summarize
read_analyze_format
calculate_format
read_summarize_format
```

工具统一注册在：

```text
configs/tools.yaml
```

每个工具配置包括：

```yaml
calculator:
  module: skills.calculator
  function: calculator
  description: Calculate a safe arithmetic expression.
  parameters:
    expression:
      type: string
      description: Arithmetic expression using numbers and supported operators.
  required: [expression]
  returns:
    result:
      type: number
      description: Calculated value.
```

这种配置化方式让 B3 不需要写死具体工具。新增 Skill 时，只要在 `tools.yaml` 中注册模块路径、函数名、参数和返回值说明，B3 就能生成 schema 并执行工具。

## 3. 快速开始

推荐从项目的 `code/` 目录运行命令：

```bash
cd code
```

### 3.1 生成 tools_schema

```bash
python b3_tool_layer.py \
  --tools_config ../configs/tools.yaml \
  --toolset basic_tools \
  --export_schema \
  --outdir ../outputs/B3_tools
```

输出文件：

```text
outputs/B3_tools/tools_schema.json
outputs/B3_tools/tool_schema_report.json
```

`tools_schema.json` 示例：

```json
{
  "type": "function",
  "function": {
    "name": "calculator",
    "description": "Calculate a safe arithmetic expression.",
    "parameters": {
      "type": "object",
      "properties": {
        "expression": {
          "type": "string",
          "description": "Arithmetic expression using numbers and supported operators."
        }
      },
      "required": ["expression"],
      "additionalProperties": false
    },
    "x-returns": {
      "type": "object",
      "properties": {
        "result": {
          "type": "number",
          "description": "Calculated value."
        }
      }
    }
  }
}
```

这一步不使用大模型，只是把工具配置转换为 JSON schema。

### 3.2 从 Python 函数签名自动生成 schema

B3 也支持从真实 Python 函数签名自动生成参数 schema：

```bash
python b3_tool_layer.py \
  --tools_config ../configs/tools.yaml \
  --toolset basic_tools \
  --export_auto_schema \
  --outdir ../outputs/B3_tools
```

输出文件：

```text
outputs/B3_tools/tools_schema_auto.json
outputs/B3_tools/tool_schema_auto_report.json
```

自动 schema 会标记：

```json
"x-schema-source": "python_signature"
```

该功能用于减少 `tools.yaml` 手写参数和真实函数签名不一致的问题。

### 3.3 执行 tool_calls

```bash
python b3_tool_layer.py \
  --tools_config ../configs/tools.yaml \
  --toolset basic_tools \
  --tool_calls ../data/messages/ai_message_with_tool_calls.json \
  --execute \
  --outdir ../outputs/B3_tools
```

输入示例：

```json
{
  "role": "assistant",
  "content": "",
  "tool_calls": [
    {
      "id": "call_001",
      "name": "file_reader",
      "args": {
        "path": "docs/agent_intro.txt",
        "max_chars": 2000
      }
    }
  ]
}
```

输出文件：

```text
outputs/B3_tools/tool_messages.json
outputs/B3_tools/tool_call_log.jsonl
outputs/B3_tools/tool_stats.json
```

输出 ToolMessage 示例：

```json
{
  "role": "tool",
  "tool_call_id": "call_001",
  "name": "file_reader",
  "content": "{\"skill_name\":\"file_reader\",\"status\":\"success\",...}",
  "status": "success"
}
```

其中 `content` 是序列化后的 SkillResult JSON 字符串。

## 4. 输入输出格式

### 4.1 AIMessage tool_calls

B3 支持项目内部简化格式：

```json
{
  "id": "call_001",
  "name": "calculator",
  "args": {
    "expression": "23 * 17 + 9"
  }
}
```

也支持 function calling 格式：

```json
{
  "id": "call_001",
  "function": {
    "name": "calculator",
    "arguments": "{\"expression\":\"23 * 17 + 9\"}"
  }
}
```

B3 会统一规范化为：

```json
{
  "id": "call_001",
  "name": "calculator",
  "args": {
    "expression": "23 * 17 + 9"
  }
}
```

### 4.2 SkillResult

所有工具执行结果统一封装为 SkillResult：

```json
{
  "skill_name": "calculator",
  "status": "success",
  "input": {
    "expression": "23 * 17 + 9"
  },
  "output": {
    "result": 400
  },
  "error": null,
  "latency_ms": 0.5
}
```

失败时：

```json
{
  "skill_name": "calculator",
  "status": "error",
  "input": {},
  "output": null,
  "error": {
    "type": "ValueError",
    "message": "missing required parameters: expression"
  },
  "latency_ms": 0.02
}
```

### 4.3 ToolMessage

B3 最终返回给 B1 的是 ToolMessage：

```json
{
  "role": "tool",
  "tool_call_id": "call_001",
  "name": "calculator",
  "content": "{\"skill_name\":\"calculator\",\"status\":\"success\",...}",
  "status": "success"
}
```

`tool_call_id` 用于关联前面的 AIMessage tool call。

## 5. 参数校验

B3 在调用 Skill 前会进行严格校验：

```text
工具名必须属于当前 toolset
必填参数不能缺失
不能包含未知参数
参数类型必须匹配 schema
数组元素类型必须匹配 items.type
bool 不能被误当作 integer 或 number
```

例如：

```json
{
  "id": "call_bad_code_executor",
  "name": "code_executor",
  "args": {
    "code": "x = 1",
    "timeout": true
  }
}
```

B3 会返回错误：

```text
parameter timeout must be integer
```

这保证了错误参数不会进入下游 Skill 内部。

## 6. 运行时参数注入

B3 会根据 Skill 函数签名自动注入运行时参数：

```text
data_root
output_dir
```

如果目标函数声明了 `data_root`，B3 会注入 `configs/tools.yaml` 中配置的数据根目录。

如果目标函数声明了 `output_dir`，B3 会注入当前 B3 输出目录。

这让 `file_reader`、`table_analyzer`、`format_converter` 等工具可以稳定处理相对路径。

## 7. 有限重试

B3 支持对可恢复错误进行有限重试。配置位于 `configs/tools.yaml`：

```yaml
settings:
  max_retries: 1
  retryable_error_types:
    - FileNotFoundError
    - TimeoutError
    - ConnectionError
```

执行日志会记录：

```json
{
  "execution_attempts": 2,
  "retry_count": 1,
  "retried": true,
  "retry_errors": [
    {
      "attempt": 1,
      "type": "TimeoutError",
      "message": "..."
    }
  ]
}
```

该机制避免临时文件访问失败或连接超时直接导致整轮工具执行失败。

## 8. 结果缓存

B3 在一次执行过程中支持 tool call 结果缓存。

缓存 key 由工具名和参数共同决定：

```text
name + args
```

如果同一轮中出现相同工具名和相同参数，B3 会复用前一次结果，而不是重复调用 Skill。

统计文件中会记录：

```text
cache_hits
cache_misses
cache_hit_rate
```

输出位置：

```text
outputs/B3_tools/tool_stats.json
```

## 9. 测试命令

### 9.1 正常 file_reader 调用

```bash
python b3_tool_layer.py \
  --tools_config ../configs/tools.yaml \
  --toolset basic_tools \
  --tool_calls ../data/messages/ai_message_with_tool_calls.json \
  --execute \
  --outdir ../outputs/B3_tools
```

### 9.2 format_converter 调用

```bash
python b3_tool_layer.py \
  --tools_config ../configs/tools.yaml \
  --toolset basic_tools \
  --tool_calls ../data/messages/b3_tool_call_format_converter_valid.json \
  --execute \
  --outdir ../outputs/B3_tools/format_converter_valid
```

### 9.3 未知工具错误

```bash
python b3_tool_layer.py \
  --tools_config ../configs/tools.yaml \
  --toolset basic_tools \
  --tool_calls ../data/messages/b3_tool_call_unknown_tool.json \
  --execute \
  --outdir ../outputs/B3_tools/unknown_tool
```

### 9.4 缺少必填参数错误

```bash
python b3_tool_layer.py \
  --tools_config ../configs/tools.yaml \
  --toolset basic_tools \
  --tool_calls ../data/messages/b3_tool_call_missing_required.json \
  --execute \
  --outdir ../outputs/B3_tools/missing_required
```

错误样例不会让 CLI 崩溃，而是生成 `status=error` 的 ToolMessage。

## 10. 主要输出文件

| 文件 | 说明 |
|---|---|
| `outputs/B3_tools/tools_schema.json` | 生成给模型看的工具 schema |
| `outputs/B3_tools/tool_schema_report.json` | schema 导出摘要 |
| `outputs/B3_tools/tools_schema_auto.json` | 从 Python 函数签名自动生成的 schema |
| `outputs/B3_tools/tool_schema_auto_report.json` | 自动 schema 导出摘要 |
| `outputs/B3_tools/tool_messages.json` | B3 执行 tool calls 后生成的 ToolMessage |
| `outputs/B3_tools/tool_call_log.jsonl` | 每个 tool call 的详细执行日志 |
| `outputs/B3_tools/tool_stats.json` | 成功率、失败率、缓存命中率、重试次数等统计 |

## 11. 与真实大模型的关系

B3 本身不加载、不运行真实大模型。

真实大模型使用发生在 B4 或 B1：

```bash
python b4_local_agent_llm.py ...
```

或：

```bash
python b1_agent_runtime.py ... --llm_mode prompt_json
```

关系可以概括为：

```text
B3 生成 tools_schema：不用模型
B4 根据 tools_schema 生成 tool_calls：使用模型
B3 执行 tool_calls：不用模型
B1 integrated 全链路：B4 部分使用模型，其余工具执行不使用模型
```

## 12. 已完成功能

```text
tools_schema 生成
Python 函数签名自动 schema 生成
tool_calls 标准化
工具名校验
必填参数校验
未知参数校验
参数类型校验
Skill 动态加载
data_root / output_dir 自动注入
可恢复错误有限重试
相同 name + args 结果缓存
SkillResult 单层封装
ToolMessage 生成
tool_call_log.jsonl 执行日志
tool_stats.json 执行统计
B1 Runtime 集成调用支持
```

## 13. 核心文件

```text
code/b3_tool_layer.py
configs/tools.yaml
code/common/schemas.py
data/messages/ai_message_with_tool_calls.json
data/messages/b3_tool_call_format_converter_valid.json
data/messages/b3_tool_call_unknown_tool.json
data/messages/b3_tool_call_missing_required.json
data/messages/b3_tool_call_new_skills_valid.json
```

## 14. 设计总结

B3 的核心价值是把大模型输出的不确定 JSON 转换为系统内部稳定、可校验、可执行、可追踪的工具调用。

它不负责模型推理，也不负责具体 Skill 的业务逻辑，而是专注于工具协议层：

```text
工具描述
参数约束
调用调度
错误封装
日志追踪
执行统计
```

通过这一层，B1 可以用统一的 ToolMessage 格式继续维护多轮 Agent 流程，B4 也可以只关注如何生成符合 schema 的工具调用。

