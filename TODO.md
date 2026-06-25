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

**注意，如果是单条消息，使用user_input；如果是多条消息，使用user_inputs**