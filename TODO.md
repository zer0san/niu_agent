# TODO

B1:
- 支持多轮用户输入，实现多次 tool_calls 循环
- 支持断点续跑，能实现状态恢复，在中断后继续执行后续LLM或工具调用。
- 支持批量任务运行，能读取一个批量任务输入文件，执行多个Agent任务
- 将历史消息压缩为摘要后继续对话
- 支持一次对话里，不同system prompt模板切换或添加

B4:
- 支持单轮AIMessage生成多个tool_calls与单轮接收多个ToolMessage
- 支持Plan-and-Execute
- 支持模型内置tools_schema传参方法，并与prompt注入tools_schema的方式进行效果对比。

# 输入格式

{
  "conversation_id": "conv_001",
  "user_input": "帮我阅读 docs/agent_intro.txt，总结三条中文要点。", (用户输入)
  "system_prompt_path": "../prompts/local_tool_agent.txt", (系统prompt模板路径)
  "selected_memory_ids": ["mem_conversation_conv_000"], (选中的记忆ID列表)
  "use_global_memory": true,
  "toolset": "basic_tools", (工具集)
  "max_turns": 3, (最大轮数)
  "save_memory": "conversation" (存储本轮对话记忆，类型：对话),
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
  ] (系统prompt模板切换规则)
}

# B1

## 多轮输入

**注意，如果是单条消息，使用user_input；如果是多条消息，使用user_inputs**

修改点：
- 输入验证函数(`_validate_runtime_input`)，支持两种格式：
  - 多轮用户输入格式：user_input
  - 单轮用户输入格式：user_inputs
- 核心循环重构(`run_agent`)，新增 `_process_user_input` 内部函数，封装单个用户输入的处理逻辑

个人演示
```
python b1_agent_runtime.py \
  --input ../data/b1_fixtures/multi_input/b1_fixture_input_multi.json \
  --outdir ../outputs/B1_fixture_multi
```

全系统演示
```
python b1_agent_runtime.py \
  --input ../data/runtime_input_multi.json \
  --tools_config ../configs/tools.yaml \
  --memory_config ../configs/memory.yaml \
  --model_config ../configs/model.yaml \
  --outdir ../output/B1_runtime
```

## prompt 切换

增加 `system_prompt_manager` 类，用于管理不同system prompt模板的切换。

输入增加`system_prompt_switches`字段，用于指定不同轮次使用的system prompt模板。
`replace`：替换当前system prompt模板，`append`：在当前system prompt模板基础上添加新内容。

个人演示
```
python b1_agent_runtime.py \
  --input ../data/b1_fixtures/prompt_change/b1_fixture_input.json \
  --outdir ../output/prompt_change_test_1
```

全系统演示
```
python b1_agent_runtime.py \
--input ../data/runtime_input_prompt.json \
--tools_config ../configs/tools.yaml \
--memory_config ../configs/memory.yaml \
--model_config ../configs/model.yaml \
--outdir ../output/runtime_prompt_change
```

## 批量任务运行

增加`--batch`参数，用于指定批量任务输入文件路径。

新增批量执行函数`run_batch_agent`
- 接收批量输入文件路径，解析出`batch_id`和任务列表
- 顺序遍历每个任务，为每个任务创建独立的输出子目录
- 调用现有的`run_agent`函数，执行每个任务

个人演示
```
python b1_agent_runtime.py \
  --input ../data/b1_fixtures/batch_input/b1_fixture_batch_input.json \
  --outdir ../output/batch_test_1 \
  --batch
```

全系统演示
```
python b1_agent_runtime.py \
--input ../data/runtime_input_batch.json \
--outdir ../output/batch_full_demo2 \
--tools_config ../configs/tools.yaml \
--memory_config ../configs/memory.yaml \
--model_config ../configs/model.yaml \
--batch
```

# B4

## 支持Plan-and-Execute

Plan-and-Execute 功能不只修改了b4，还修改了b1。
运行时需要运行b1

- 新增决策模式 ：在 runtime_input.json 中添加 decision_mode 字段
- 规划器实现 ：在 b4_local_agent_llm.py 中添加规划模式
- 执行器实现 ：在 b1_agent_runtime.py 中添加计划执行循环
- 规划提示模板 ：新增 planner.txt 和 executor.txt 提示模板

具体实现：
- 在common/schemas.py中添加PlanStep和ExecutionPlan的构造和验证函数
- 在b4_local_agent_llm.py中添加规划模式的实现
- 在b1_agent_runtime.py中添加计划执行循环

运行测试：

运行 Plan-and-Execute 模式测试
```
python code/b1_agent_runtime.py \
    --input data/server_test_plan.json \
    --tools_config configs/tools.yaml \
    --memory_config configs/memory.yaml \
    --model_config configs/model.yaml \
    --outdir output/server_plan_test
```

运行 ReAct 模式测试
```
python code/b1_agent_runtime.py \
    --input data/server_test_react.json \
    --tools_config configs/tools.yaml \
    --memory_config configs/memory.yaml \
    --model_config configs/model.yaml \
    --outdir output/server_react_test
```

## 单轮AIMessage生成多个tool_calls与单轮接收多个ToolMessage

修改prompt，给prompt添加一些示例，允许模型生成多个tool_calls。

修改local_tool_agent.txt

增加了工具调用失败的处理策略：
- abort：如果有一个工具调用失败，整个任务失败。
- continue：如果有一个工具调用失败，继续执行其他工具调用。

```
python code/b1_agent_runtime.py \
  --input data/runtime_input_multi_tool.json \
  --tools_config configs/tools.yaml \
  --memory_config configs/memory.yaml \
  --model_config configs/model.yaml \
  --outdir output/multi_tool_test
```