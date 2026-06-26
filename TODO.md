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
  "save_memory": "conversation" (存储本轮对话记忆，类型：对话)
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
  --input ../data/b1_fixtures/b1_fixture_input_multi.json \
  --outdir ../outputs/B1_fixture_multi
```

全系统演示
```
python b1_agent_runtime.py \
  --input ../data/runtime_input_multi.json \
  --tools_config ../configs/tools.yaml \
  --memory_config ../configs/memory.yaml \
  --model_config ../configs/model.yaml \
  --outdir ../outputs/B1_runtime
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

