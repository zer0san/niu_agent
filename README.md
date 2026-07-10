# Local Agent Framework

一个基于 Python 3.10 实现的本地文件驱动的 Agent 框架，支持多轮对话、工具调用、记忆管理和批量任务执行。

## 特性

- **模块化架构**: B1-B5 五个独立模块，职责清晰，可独立运行或组合使用
- **多决策模式**: 支持 React 和 Plan-and-Execute 两种决策模式
- **工具调用**: 支持单工具和多工具并行调用
- **记忆系统**: 支持关键词搜索和向量检索的混合检索策略
- **历史压缩**: 自动压缩历史对话以控制上下文长度
- **批量任务**: 支持批量执行多个 Agent 任务
- **Mock 模式**: 支持无 GPU/无模型环境下的调试

## 目录结构

```
agent_jiang/
├── code/                    # 核心代码模块
│   ├── b1_agent_runtime.py  # Agent 总控与执行循环
│   ├── b2_run_skill.py      # Skill 独立运行器
│   ├── b3_tool_layer.py     # 工具层（Schema生成与执行）
│   ├── b4_local_agent_llm.py # LLM 调用与解析
│   ├── b5_memory.py         # 记忆管理模块
│   ├── run_full_demo.py     # 完整演示入口
│   ├── system_prompt_manager.py  # 系统提示词管理器
│   └── common/              # 公共工具类
│       ├── io_utils.py      # IO 操作工具
│       ├── logging_utils.py # 日志工具
│       ├── path_utils.py    # 路径解析工具
│       └── schemas.py       # 数据模型定义
├── configs/                 # 配置文件
│   ├── tools.yaml           # 工具配置
│   ├── memory.yaml          # 记忆配置
│   ├── model.yaml           # 模型配置
│   └── memory_small_limit.yaml # 小限制记忆配置
├── data/                    # 数据目录
│   ├── docs/                # 文档文件
│   ├── messages/            # 消息示例
│   ├── tables/              # 表格数据
│   ├── tool_inputs/         # 工具输入示例
│   ├── memory_inputs/       # 记忆输入示例
│   ├── b1_fixtures/         # B1 测试数据
│   └── b4_fixtures/         # B4 测试数据
├── memory/                  # 记忆存储
│   ├── conversations/       # 对话记忆
│   ├── global/              # 全局记忆
│   └── milvus_memory.db/    # Milvus 向量数据库
├── prompts/                 # 提示词模板
├── skills/                  # Skill 实现
│   ├── calculator.py        # 计算器
│   ├── file_reader.py       # 文件读取
│   ├── local_file_search.py # 文件搜索
│   ├── table_analyzer.py    # 表格分析
│   ├── format_converter.py  # 格式转换
│   ├── code_executor.py     # 代码执行
│   └── text_summarizer.py   # 文本摘要
├── embedding_models/        # 嵌入模型
├── models/                  # LLM 模型目录
├── output/                  # 输出目录
└── requirements.txt         # 依赖列表
```

## 安装指南

### 环境要求

- Python 3.10+
- CUDA 11.8（GPU 推理）

### 步骤

```bash
# 创建并激活 conda 环境
conda create -n agent_env python=3.10 -y
conda activate agent_env

# 禁止加载用户级 site-packages
export PYTHONNOUSERSITE=1

# 安装依赖
pip install -r requirements.txt
```

### 模型配置

1. 下载 Qwen3.5-4B 模型到本地
2. 修改 `configs/model.yaml` 中的 `model_name_or_path` 和 `tokenizer_name_or_path` 为模型路径

## 模块说明

### B1: Agent Runtime (`code/b1_agent_runtime.py`)

Agent 总控模块，负责：
- 消息序列管理
- 执行循环控制
- 多轮输入支持
- 决策模式选择（React / Plan-and-Execute）
- 历史对话压缩
- 产物汇总与保存

**执行模式**:
- `fixture`: 使用预设数据进行隔离演示
- `integrated`: 调用 B3/B4/B5 进行完整执行

### B2: Skill Runner (`code/b2_run_skill.py`)

独立运行工具技能，支持：
- **基础技能**:
  - 计算器 (calculator)
  - 文件读取 (file_reader)
  - 文件搜索 (local_file_search)
  - 表格分析 (table_analyzer)
  - 格式转换 (format_converter)
  - 代码执行 (code_executor)
  - 文本摘要 (text_summarizer)
- **复合技能**（链式调用多个基础技能）:
  - search_read_summarize: 搜索 → 读取 → 生成摘要
  - read_analyze_format: 读取表格 → 分析 → 格式化输出
  - calculate_format: 计算表达式 → 格式化结果
  - read_summarize_format: 读取文件 → 生成摘要 → 格式化报告

### B3: Tool Layer (`code/b3_tool_layer.py`)

工具层模块，负责：
- 根据配置生成 OpenAI 风格的工具 Schema
- 校验工具调用参数（类型、必填、数组元素）
- 执行工具调用并返回结构化结果
- **工具执行缓存**: 相同工具调用自动复用结果
- **重试机制**: 支持配置重试次数和可重试错误类型
- **执行统计**: 生成 `tool_stats.json` 记录调用统计信息

### B4: Local Agent LLM (`code/b4_local_agent_llm.py`)

LLM 调用模块，负责：
- 加载本地 LLM 模型
- 构造提示词并调用模型
- 解析模型输出为标准 AIMessage
- 支持多种解析策略（JSON、代码块、纯文本）
- 生成执行计划（Plan-and-Execute 模式）

### B5: Memory (`code/b5_memory.py`)

记忆管理模块，支持：
- 关键词检索（BM25）
- 向量检索（Milvus）
- 混合检索（RRF 融合）
- 记忆保存与索引维护
- 记忆截断与摘要


## 数据格式

### AIMessage

```json
{
  "role": "assistant",
  "content": "最终回答内容",
  "tool_calls": [
    {"id": "call_001", "name": "tool_name", "args": {...}}
  ]
}
```

### ToolMessage

```json
{
  "role": "tool",
  "tool_call_id": "call_001",
  "name": "tool_name",
  "content": "{\"skill_name\":\"...\",\"status\":\"success\",...}",
  "status": "success"
}
```

### SkillResult

```json
{
  "skill_name": "calculator",
  "status": "success",
  "input": {"expression": "2+2"},
  "output": {"result": 4},
  "error": null,
  "latency_ms": 1.5
}
```

## 配置文件说明

### `configs/tools.yaml`

定义可用工具集和工具参数：

```yaml
tools:
  calculator:
    module: skills.calculator
    function: calculator
    description: 数学计算器
    parameters:
      expression:
        type: string
        description: 数学表达式
    required: [expression]

toolsets:
  basic_tools:
    - calculator
    - file_reader
    - local_file_search
```

### `configs/memory.yaml`

配置记忆系统：

```yaml
memory:
  root_dir: ../memory
  global_memory_dir: global
  conversation_memory_dir: conversations
  index_path: memory_index.json
  max_memory_chars: 4000
  history_compress_keep_recent: 2
  history_compress_max_sentences: 3
  enable_history_compress: true

vector_memory:
  enabled: true
  backend: milvus
  db_path: ../memory/milvus_memory.db

keyword_memory:
  enabled: true
  backend: sqlite_bm25
```

### `configs/model.yaml`

配置 LLM 模型：

```yaml
model:
  model_name_or_path: ../models/Qwen3.5-4B
  tokenizer_name_or_path: ../models/Qwen3.5-4B
  torch_dtype: bfloat16
  device_map: auto
  local_files_only: true

generation:
  max_new_tokens: 1024
  do_sample: false

runtime:
  default_mode: prompt_json
```

## CLI 退出码

| 退出码 | 含义 |
|--------|------|
| 0 | 成功，或业务错误已被捕获并写入结构化产物 |
| 1 | 致命错误（配置、输入文件、解析、模块加载等） |
| 2 | 参数使用错误 |

## 许可证

MIT License
