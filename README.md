# B1 + B4 模块 - Agent运行时与本地LLM推理

  

本项目包含两个核心模块：**B1 Agent运行时** 和 **B4 本地LLM推理与规划**，是智能Agent系统的核心组件。

  

## 目录

  

- [B1 + B4 模块 - Agent运行时与本地LLM推理](#b1--b4-模块---agent运行时与本地llm推理)

  - [目录](#目录)

  - [模块架构](#模块架构)

  - [核心文件说明](#核心文件说明)

  - [B1模块 - Agent运行时](#b1模块---agent运行时)

    - [功能特性](#功能特性)

    - [执行流程](#执行流程)

    - [输入格式](#输入格式)

    - [使用方法](#使用方法)

  - [B4模块 - 本地LLM推理](#b4模块---本地llm推理)

    - [功能特性](#功能特性-1)

    - [推理流程](#推理流程)

    - [使用方法](#使用方法-1)

  - [进阶功能](#进阶功能)

    - [多轮输入](#多轮输入)

    - [Prompt动态切换](#prompt动态切换)

    - [批量任务运行](#批量任务运行)

    - [历史消息压缩](#历史消息压缩)

    - [Plan-and-Execute模式](#plan-and-execute模式)

    - [单轮多工具调用](#单轮多工具调用)

  - [配置文件](#配置文件)

    - [model.yaml](#modelyaml)

    - [tools.yaml](#toolsyaml)

    - [memory.yaml](#memoryyaml)

  - [依赖安装](#依赖安装)

  - [测试与验证](#测试与验证)

    - [Fixture测试（单元测试）](#fixture测试单元测试)

    - [集成测试](#集成测试)

  - [输出文件说明](#输出文件说明)

  - [许可证](#许可证)

  

## 模块架构

  

```

┌─────────────────────────────────────────────────────────────────┐

│                        用户输入层                                │

│  ┌─────────────────┐  ┌─────────────────┐                      │

│  │  single input   │  │   multi inputs  │                      │

│  │  batch inputs   │  │  prompt switches│                      │

│  └────────┬────────┘  └────────┬────────┘                      │

└───────────┼────────────────────┼───────────────────────────────┘

            │                    │

            ▼                    ▼

┌─────────────────────────────────────────────────────────────────┐

│                    B1 - Agent运行时核心                          │

│  ┌───────────────────────────────────────────────────────────┐  │

│  │  _validate_runtime_input() ──→ 输入验证与格式兼容          │  │

│  │  run_agent()               ──→ ReAct/Plan执行循环          │  │

│  │  run_batch_agent()         ──→ 批量任务调度               │  │

│  │  SystemPromptManager       ──→ Prompt动态切换             │  │

│  │  History Compress          ──→ 历史消息压缩               │  │

│  └────────────────────┬──────────────────────────────────────┘  │

└───────────────────────┼─────────────────────────────────────────┘

                        │

        ┌───────────────┼───────────────┐

        ▼               ▼               ▼

┌───────────────┐ ┌───────────────┐ ┌───────────────┐

│   B4 - LLM    │ │   B3 - 工具层  │ │   B5 - 记忆层  │

│  推理与规划    │ │  execute_tool  │ │  load_memory  │

└───────────────┘ └───────────────┘ └───────────────┘

        │

        ▼

┌─────────────────────────────────────────────────────────────────┐

│                    本地大语言模型（LLM）                          │

│  ┌───────────────────────────────────────────────────────────┐  │

│  │  transformers + AutoModelForCausalLM                      │  │

│  │  模型缓存机制 + 多解析策略                                  │  │

│  └───────────────────────────────────────────────────────────┘  │

└─────────────────────────────────────────────────────────────────┘

```

  

## 核心文件说明

  

| 文件 | 模块 | 说明 |

| :--- | :--- | :--- |

| [b1_agent_runtime.py](file:///f:/project/niu/agent_jiang/code/b1_agent_runtime.py) | B1 | Agent运行时核心，协调各模块执行 |

| [b4_local_agent_llm.py](file:///f:/project/niu/agent_jiang/code/b4_local_agent_llm.py) | B4 | 本地LLM推理引擎，生成AIMessage和执行计划 |

| [system_prompt_manager.py](file:///f:/project/niu/agent_jiang/code/system_prompt_manager.py) | B1 | System Prompt管理器，支持动态切换 |

| [common/schemas.py](file:///f:/project/niu/agent_jiang/code/common/schemas.py) | 公共 | 数据模型验证，AIMessage和PlanStep格式校验 |

  

---

  

## B1模块 - Agent运行时

  

### 功能特性

  

- **输入验证**：支持单轮 `user_input` 和多轮 `user_inputs` 两种格式

- **执行循环**：经典ReAct模式，LLM调用→工具执行→结果反馈的完整闭环

- **消息压缩**：自动压缩历史消息，控制上下文长度

- **批量处理**：支持一次性提交多个任务进行批量执行

- **Prompt切换**：支持在对话过程中动态切换system prompt模板

  

### 执行流程

  

```

1. 加载配置文件（system_prompt_path, toolset, max_turns等）

2. 验证输入格式，兼容单轮/多轮输入

3. 加载记忆（调用B5模块）

4. 构建初始消息列表 [system, user]

5. 进入主循环：

   ├── 调用B4生成AIMessage

   ├── 解析工具调用

   ├── 调用B3执行工具

   ├── 将结果加入对话历史

   └── 重复直到无工具调用或达到max_turns

6. 保存结果（messages.json, trace.json, final_answer.md）

7. 可选：保存记忆（调用B5模块）

```

  

### 输入格式

  

**单轮输入**（`user_input`）：

  

```json

{

  "conversation_id": "conv_001",

  "user_input": "帮我阅读 docs/agent_intro.txt，总结三条中文要点。",

  "system_prompt_path": "../prompts/local_tool_agent.txt",

  "toolset": "basic_tools",

  "max_turns": 3,

  "save_memory": "conversation"

}

```

  

**多轮输入**（`user_inputs`）：

  

```json

{

  "conversation_id": "conv_multi_001",

  "user_inputs": [

    "帮我阅读 docs/agent_intro.txt，总结三条中文要点。",

    "请分析 tables/results.csv 表格"

  ],

  "system_prompt_path": "../prompts/local_tool_agent.txt",

  "toolset": "basic_tools",

  "max_turns": 10,

  "save_memory": "conversation"

}

```

  

### 使用方法

  

**单任务执行**：

  

```powershell

python code/b1_agent_runtime.py `

  --input data/runtime_input.json `

  --tools_config configs/tools.yaml `

  --memory_config configs/memory.yaml `

  --model_config configs/model.yaml `

  --outdir output/single_task_test

```

  

**批量任务执行**：

  

```powershell

python code/b1_agent_runtime.py `

  --input data/runtime_input_batch.json `

  --tools_config configs/tools.yaml `

  --memory_config configs/memory.yaml `

  --model_config configs/model.yaml `

  --outdir output/batch_test `

  --batch

```

  

---

  

## B4模块 - 本地LLM推理

  

### 功能特性

  

- **本地模型加载**：使用transformers库加载本地大语言模型

- **模型缓存**：支持模型缓存，避免重复加载，提升推理效率

- **多解析策略**：5层回退解析策略，提高输出格式兼容性

- **Plan-and-Execute**：支持生成详细执行计划

- **多工具调用**：支持单轮生成多个工具调用

  

### 推理流程

  

```

1. 加载模型配置（model.yaml）

2. 验证消息格式（调用common/schemas.py）

3. 构建提示消息：

   ├── 追加工具schema到system消息

   ├── 添加格式说明到user消息

   └── 如果是tool消息后，追加引导提醒

4. 编码提示（tokenizer.apply_chat_template）

5. 调用模型推理（model.generate）

6. 解码输出（tokenizer.decode）

7. 解析输出：

   ├── 标准JSON → 尾部反引号处理 → tool_calls片段提取

   ├── Markdown代码块 → 纯文本回退

8. 验证AIMessage格式

9. 返回结果

```

  

### 使用方法

  

**直接调用B4模块**：

  

```powershell

python code/b4_local_agent_llm.py `

  --model_config configs/model.yaml `

  --messages data/test_messages.json `

  --tools_schema data/tools_schema_basic.json `

  --outdir output/b4_test

```

  

---

  

## 进阶功能

  

### 多轮输入

  

**实现思路**：

  

1. **格式检测**：在输入验证阶段，检查 `user_input` 和 `user_inputs` 字段的存在

2. **归一化处理**：多轮输入时，将第一个输入作为初始 `user_input`，其余输入在后续轮次中依次处理

3. **迭代执行**：核心循环支持在完成一轮对话后，继续处理下一个用户输入

  

**输入示例**：

  

```json

{

  "conversation_id": "conv_multi_001",

  "user_inputs": [

    "帮我阅读 docs/agent_intro.txt，总结三条中文要点。",

    "请分析 tables/results.csv 表格",

    "帮我写一个Python函数来计算平均值"

  ],

  "system_prompt_path": "../prompts/local_tool_agent.txt",

  "toolset": "basic_tools",

  "max_turns": 10,

  "save_memory": "conversation"

}

```

  

### Prompt动态切换

  

**实现思路**：

  

1. **管理器模式**：引入 `SystemPromptManager` 类封装prompt的加载、切换和历史记录

2. **两种切换模式**：

   - **replace**：完全替换当前prompt

   - **append**：在当前prompt基础上追加新内容

3. **切换时机控制**：通过 `system_prompt_switches` 配置数组，指定在第几个用户输入后执行切换

  

**输入示例**：

  

```json

{

  "user_inputs": [

    "帮我阅读 docs/agent_intro.txt，总结三条中文要点。",

    "请分析 tables/results.csv 表格",

    "帮我写一个Python函数来计算平均值"

  ],

  "system_prompt_switches": [

    {

      "after_user_input": 0,

      "switch_to": "../prompts/researcher.txt",

      "mode": "append"

    },

    {

      "after_user_input": 1,

      "switch_to": "../prompts/coding_assistant.txt",

      "mode": "replace"

    }

  ]

}

```

  

### 批量任务运行

  

**实现思路**：

  

1. **批量输入格式**：定义包含 `batch_id` 和 `tasks` 数组的JSON格式

2. **任务隔离**：为每个任务创建独立的输出子目录

3. **顺序执行**：按顺序遍历任务列表，调用现有的 `run_agent()` 函数

4. **结果汇总**：生成批量执行摘要文件

  

**输入示例**：

  

```json

{

  "batch_id": "batch_demo_001",

  "tasks": [

    {

      "conversation_id": "task_001",

      "user_input": "帮我阅读 docs/agent_intro.txt",

      "system_prompt_path": "../prompts/local_tool_agent.txt",

      "toolset": "basic_tools",

      "max_turns": 3,

      "save_memory": "none"

    },

    {

      "conversation_id": "task_002",

      "user_input": "帮我计算56*29+81",

      "system_prompt_path": "../prompts/local_tool_agent.txt",

      "toolset": "basic_tools",

      "max_turns": 2,

      "save_memory": "none"

    }

  ]

}

```

  

### 历史消息压缩

  

**实现思路**：

  

1. **触发机制**：当历史消息总字符数达到 `max_memory_chars` 的80%时自动触发

2. **三阶段压缩策略**：

   - **保留头部**：始终保留第一条system消息

   - **保留尾部**：保留最近N轮完整对话（可配置，默认2轮）

   - **压缩中部**：将中间的历史消息转换为纯文本，调用text_summary生成摘要

3. **摘要插入**：将摘要作为system角色消息插入

  

**配置参数**（在 `configs/memory.yaml` 中）：

  

```yaml

memory:

  max_memory_chars: 2000                    # 达到80%时触发压缩

  history_compress_keep_recent: 2           # 保留最近N轮完整对话

  history_compress_max_sentences: 3         # 摘要最大句子数

  enable_history_compress: true             # 是否启用压缩

```

  

### Plan-and-Execute模式

  

**实现思路**：

  

1. **规划阶段**：使用专门的planner prompt引导模型生成详细执行计划

2. **计划验证**：验证计划中引用的工具是否在当前toolset中可用

3. **执行阶段**：按顺序执行计划中的每个步骤

  

**输入示例**：

  

```json

{

  "conversation_id": "conv_plan_001",

  "user_input": "分析 docs/agent_intro.txt 和 tables/results.csv，然后总结它们的内容",

  "system_prompt_path": "../prompts/planner.txt",

  "toolset": "basic_tools",

  "max_turns": 10,

  "save_memory": "conversation",

  "decision_mode": "plan"

}

```

  

### 单轮多工具调用

  

**实现思路**：

  

1. **Prompt增强**：在格式说明中增加多工具调用的示例

2. **格式解析**：支持解析包含多个工具调用的JSON数组

3. **工具失败策略**：支持 `abort`（失败即终止）和 `continue`（继续执行）两种策略

  

**输入示例**：

  

```json

{

  "conversation_id": "conv_multi_tool_001",

  "user_input": "帮我同时阅读 docs/agent_intro.txt 和 docs/search_skill_demo.md",

  "system_prompt_path": "../prompts/local_tool_agent.txt",

  "toolset": "basic_tools",

  "max_turns": 3,

  "save_memory": "none",

  "tool_failure_policy": "continue"

}

```

  

---

  

## 配置文件

  

### model.yaml

  

```yaml

model:

  model_name_or_path: ../models/your-model  # 模型路径

  tokenizer_name_or_path: ../models/your-model  # Tokenizer路径

  local_files_only: true

  trust_remote_code: false

  torch_dtype: auto

  device_map: auto

  

generation:

  max_new_tokens: 1024

  do_sample: false

  

runtime:

  default_mode: prompt_json  # mock 或 prompt_json

```

  

### tools.yaml

  

```yaml

tools:

  file_reader:

    name: file_reader

    description: "读取本地文件内容"

    parameters:

      path:

        type: string

        description: "文件路径"

      max_chars:

        type: integer

        description: "最大读取字符数"

  

toolsets:

  basic_tools:

    - file_reader

    - calculator

    - table_analyzer

```

  

### memory.yaml

  

```yaml

memory:

  root_dir: ../memory

  max_memory_chars: 2000

  history_compress_keep_recent: 2

  history_compress_max_sentences: 3

  enable_history_compress: true

```

  

---

  

## 依赖安装

  

```powershell

# 创建虚拟环境（建议）

python -m venv venv

.\venv\Scripts\activate

  

# 安装依赖

pip install -r requirements.txt

```

  

**核心依赖说明**：

  

| 依赖 | 版本 | 用途 |

| :--- | :--- | :--- |

| torch | 2.7.1+cu118 | 深度学习框架，GPU加速推理 |

| transformers | 5.12.1 | 模型加载和推理 |

| accelerate | 1.14.0 | 分布式推理支持 |

| PyYAML | 6.0.3 | YAML配置文件解析 |

| jieba | >=0.42 | 中文分词 |

| pymilvus | 2.6.3 | 向量数据库 |

  

---

  

## 测试与验证

  

### Fixture测试（单元测试）

  

```powershell

# 多轮输入测试

python code/b1_agent_runtime.py `

  --input data/b1_fixtures/multi_input/b1_fixture_input_multi.json `

  --outdir output/multi_input_test

  

# Prompt切换测试

python code/b1_agent_runtime.py `

  --input data/b1_fixtures/prompt_change/b1_fixture_input.json `

  --outdir output/prompt_change_test

  

# 批量任务测试

python code/b1_agent_runtime.py `

  --input data/b1_fixtures/batch_input/b1_fixture_batch_input.json `

  --outdir output/batch_test `

  --batch

  

# 历史压缩测试

python code/b1_agent_runtime.py `

  --input data/b1_fixtures/history_compress/b1_fixture_input.json `

  --outdir output/history_compress_test

  

# Plan-and-Execute测试

python code/b1_agent_runtime.py `

  --input data/b4_fixtures/b4_fixtures_plan/b4_fixture_input.json `

  --outdir output/plan_test

  

# 多工具调用测试

python code/b1_agent_runtime.py `

  --input data/b4_fixtures/multi_tool/b4_fixture_input_multi_tool.json `

  --outdir output/multi_tool_test

```

  

### 集成测试

  

```powershell

# ReAct模式测试

python code/b1_agent_runtime.py `

  --input data/server_test_react.json `

  --tools_config configs/tools.yaml `

  --memory_config configs/memory.yaml `

  --model_config configs/model.yaml `

  --outdir output/server_react_test

  

# Plan模式测试

python code/b1_agent_runtime.py `

  --input data/server_test_plan.json `

  --tools_config configs/tools.yaml `

  --memory_config configs/memory.yaml `

  --model_config configs/model.yaml `

  --outdir output/server_plan_test

  

# 多工具调用测试

python code/b1_agent_runtime.py `

  --input data/runtime_input_multi_tool.json `

  --tools_config configs/tools.yaml `

  --memory_config configs/memory.yaml `

  --model_config configs/model.yaml `

  --outdir output/multi_tool_server_test

  

# 批量任务测试

python code/b1_agent_runtime.py `

  --input data/runtime_input_batch.json `

  --tools_config configs/tools.yaml `

  --memory_config configs/memory.yaml `

  --model_config configs/model.yaml `

  --outdir output/batch_server_test `

  --batch

```

  

---

  

## 输出文件说明

  

每个任务执行后，输出目录包含以下文件：

  

| 文件                   | 说明                   |
| :------------------- | :------------------- |
| `messages.json`      | 完整对话消息历史             |
| `trace.json`         | 执行轨迹（轮次、状态、错误信息）     |
| `final_answer.md`    | 最终回答                 |
| `tool_messages.json` | 工具执行结果（integrated模式） |
| `runtime_log.jsonl`  | 运行日志（integrated模式）   |
| `llm_calls/`         | LLM调用记录（原始输出、解析结果）   |


  

---

  

## 许可证

  

MIT License
