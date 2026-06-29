from __future__ import annotations

import argparse
import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from common.io_utils import append_jsonl, read_json, read_yaml, write_json
from common.logging_utils import now_iso
from common.path_utils import resolve_cli_path, resolve_from_file
from common.schemas import make_ai_message, validate_ai_message, validate_messages

# 当模型输出无法解析时使用的默认错误内容
PARSE_ERROR_CONTENT = "模型输出解析失败，无法生成有效工具调用或最终回答。"
# 全局模型缓存，避免重复加载模型，键是模型配置的元组，值是(tokenizer, model)元组
_MODEL_CACHE: dict[tuple[str, ...], tuple[Any, Any]] = {}

# 加载并验证模型配置文件
def _load_model_config(model_config: str | Path) -> tuple[Path, dict]:
    path = Path(model_config).resolve()
    config = read_yaml(path)
    if not isinstance(config, dict):
        raise ValueError("model.yaml must contain an object")
    return path, config

# 生成模型推理产物的文件路径
def _artifact_paths(artifact_dir: str | Path, stem: str | None) -> tuple[Path, Path, Path]:
    directory = Path(artifact_dir)
    prefix = f"{stem}_" if stem else ""
    return (
        directory / f"{prefix}raw_model_output.json",
        directory / f"{prefix}ai_message.json",
        directory / "llm_run_log.jsonl",
    )

# 从工具消息中提取工具执行结果
def _extract_tool_result(message: dict) -> dict:
    try:
        result = json.loads(message["content"])
    except (KeyError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("ToolMessage content is not a SkillResult JSON string") from exc
    if not isinstance(result, dict):
        raise ValueError("ToolMessage content must decode to an object")
    return result

# 从工具结果中提取三条中文要点
def _three_points(text: str) -> list[str]:
    parts = [part.strip(" \t\r\n。") for part in re.split(r"\n+|(?<=[。！？!?])", text) if part.strip()]
    points = []
    for part in parts:
        if part not in points:
            points.append(part)
        if len(points) == 3:
            break
    while len(points) < 3:
        points.append("工具结果未提供更多可提取内容")
    return points

# 模型输出解析，将模型的原始输出解析为AIMessage格式
def _parse_tool_calls_fragment(raw_text: str, original_error: json.JSONDecodeError) -> dict:
    markers = ['"tool_calls":[', '\\"tool_calls\\":[']
    marker_index = -1
    marker = ""
    for item in markers:
        marker_index = raw_text.find(item)
        if marker_index != -1:
            marker = item
            break
    if marker_index == -1:
        raise original_error
    array_start = marker_index + marker.index("[")
    array_end = raw_text.rfind("]")
    if array_end < array_start:
        raise ValueError("model output contains tool_calls marker but no closing array")
    array_text = raw_text[array_start : array_end + 1]
    try:
        tool_calls = json.loads(array_text)
    except json.JSONDecodeError:
        tool_calls = json.loads(array_text.replace('\\"', '"'))
    if not isinstance(tool_calls, list) or not tool_calls:
        raise original_error
    return {"content": "", "tool_calls": tool_calls}

# 处理尾部多余的反引号
def _parse_json_with_backtick_tail(raw_text: str, original_error: json.JSONDecodeError) -> dict:
    text = raw_text.strip()
    try:
        candidate, end_index = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError:
        raise original_error
    trailing = text[end_index:].strip()
    if trailing and set(trailing) <= {"`"}:
        return candidate
    raise original_error

# 将解析后的候选对象转换为标准AIMessage
def _candidate_to_message(candidate: dict) -> tuple[dict, dict]:
    if not isinstance(candidate, dict):
        raise ValueError("model output JSON must be an object")
    expected_keys = {"content", "tool_calls"}
    unknown_keys = set(candidate) - expected_keys
    if unknown_keys:
        raise ValueError(f"model output JSON contains unknown keys: {', '.join(sorted(unknown_keys))}")
    message = {
        "role": "assistant",
        "content": candidate.get("content", ""),
        "tool_calls": candidate.get("tool_calls", []),
    }
    validate_ai_message(message)
    has_content = bool(message["content"].strip())
    has_tool_calls = bool(message["tool_calls"])
    if has_content == has_tool_calls:
        raise ValueError("model output must contain either final content or tool calls, but not both")
    parsed_candidate = {"content": message["content"], "tool_calls": message["tool_calls"]}
    return parsed_candidate, message

# 组合多种解析策略
def _parse_model_output(raw_text: str) -> tuple[dict, dict]:
    try:
        candidate = json.loads(raw_text.strip())
    except json.JSONDecodeError as exc:
        try:
            candidate = _parse_json_with_backtick_tail(raw_text, exc)
        except json.JSONDecodeError:
            candidate = _parse_tool_calls_fragment(raw_text, exc)
    return _candidate_to_message(candidate)

# 将字符串类型的dtype转换为对应的torch.dtype
def _dtype_value(torch_module: Any, configured: str) -> Any:
    if configured == "auto":
        return "auto"
    mapping = {
        "bfloat16": torch_module.bfloat16,
        "float16": torch_module.float16,
        "float32": torch_module.float32,
    }
    if configured not in mapping:
        raise ValueError(f"unsupported torch_dtype: {configured}")
    return mapping[configured]

# 生成模型缓存的唯一键
def _model_cache_key(
    model_path: Path,
    tokenizer_path: Path,
    local_only: bool,
    trust_remote_code: bool,
    dtype: Any,
    device_map: Any,
    max_memory: Any,
) -> tuple[str, ...]:
    try:
        device_map_key = json.dumps(device_map, sort_keys=True, separators=(",", ":"))
    except TypeError:
        device_map_key = repr(device_map)
    try:
        max_memory_key = json.dumps(max_memory, sort_keys=True, separators=(",", ":"))
    except TypeError:
        max_memory_key = repr(max_memory)
    return (
        str(model_path),
        str(tokenizer_path),
        str(local_only),
        str(trust_remote_code),
        str(dtype),
        device_map_key,
        max_memory_key,
    )

# 带缓存的模型加载
def _load_model_bundle(
    auto_model: Any,
    auto_tokenizer: Any,
    model_path: Path,
    tokenizer_path: Path,
    local_only: bool,
    trust_remote_code: bool,
    dtype: Any,
    device_map: Any,
    max_memory: Any,
) -> tuple[Any, Any]:
    cache_key = _model_cache_key(
        model_path,
        tokenizer_path,
        local_only,
        trust_remote_code,
        dtype,
        device_map,
        max_memory,
    )
    cached = _MODEL_CACHE.get(cache_key)
    if cached is not None:
        print("model_cache=hit", file=sys.stderr, flush=True)
        return cached

    print("model_cache=miss", file=sys.stderr, flush=True)
    tokenizer = auto_tokenizer.from_pretrained(
        str(tokenizer_path),
        local_files_only=local_only,
        trust_remote_code=trust_remote_code,
    )
    model = auto_model.from_pretrained(
        str(model_path),
        local_files_only=local_only,
        trust_remote_code=trust_remote_code,
        dtype=dtype,
        device_map=device_map,
        max_memory=max_memory,
    )
    _MODEL_CACHE[cache_key] = (tokenizer, model)
    return tokenizer, model

# 构建发送给LLM的完整提示
def _build_prompt_messages(messages: list[dict], tools_schema: list[dict]) -> list[dict]:
    prompt_messages = deepcopy(messages)
    format_instruction = (
        "IMPORTANT OUTPUT FORMAT:\n"
        "You must return exactly one valid JSON object.\n"
        "Do not output markdown.\n"
        "Do not output explanations.\n"
        "Do not output code fences or backticks.\n"
        'The first output character must be "{" and the last output character must be "}".\n\n'
        "Valid schema A:\n"
        '{"content":"final answer text","tool_calls":[]}\n\n'
        "Valid schema B:\n"
        '{"content":"","tool_calls":[{"id":"call_001","name":"file_reader",'
        '"args":{"path":"docs/agent_intro.txt","max_chars":2000}}]}\n\n'
        "The top-level keys must be exactly:\n"
        "- content: string\n"
        "- tool_calls: array\n\n"
        "Never put tool_calls inside content.\n"
        'Never output {"content":"tool_calls": ...}.'
    )
    envelope_reminder = (
        "IMPORTANT OUTPUT FORMAT: Output the JSON object now. "
        'Your first output character must be "{" and your last output character must be "}". '
        "Never output a backtick, Markdown, a code block, an explanation, or text outside the JSON. "
        'Use exactly the top-level keys "content" (string) and "tool_calls" (array). '
        "Choose exactly one schema: final content with an empty tool_calls array, or empty content with tool calls. "
        'Never put tool_calls inside content. Never output {"content":"tool_calls": ...}.'
    )
    system_instruction = (
        "\n\nAvailable tools JSON schema:\n"
        + json.dumps(tools_schema, ensure_ascii=False)
        + "\n"
        + format_instruction
    )
    # 将系统指令追加到第一条system消息
    if prompt_messages and prompt_messages[0].get("role") == "system":
        prompt_messages[0]["content"] += system_instruction
    else:
        prompt_messages.insert(0, {"role": "system", "content": system_instruction.strip()})
    # 将信封提醒追加到最后一条user消息
    for message in reversed(prompt_messages):
        if message.get("role") == "user":
            message["content"] += "\n\n" + envelope_reminder
            break
    # 如果最后一条消息是tool消息，追加引导提醒
    if prompt_messages[-1].get("role") == "tool":
        prompt_messages.append(
            {
                "role": "user",
                "content": (
                    envelope_reminder
                    + " The latest ToolMessage already contains a tool result. If it provides the requested "
                    'information, answer with schema A now and set "tool_calls" to exactly []. Do not repeat the '
                    "completed tool call."
                ),
            }
        )
    return prompt_messages

# 调用本地模型进行推理
def _prompt_json_generate(config_path: Path, config: dict, messages: list[dict], tools_schema: list[dict]) -> str:
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        raise RuntimeError("prompt_json mode requires requirements-llm.txt") from exc
    # 读取配置
    model_config = config.get("model", {})
    generation_config = config.get("generation", {})
    model_setting = model_config.get("model_name_or_path")
    tokenizer_setting = model_config.get("tokenizer_name_or_path", model_setting)
    if not isinstance(model_setting, str) or not isinstance(tokenizer_setting, str):
        raise ValueError("model_name_or_path and tokenizer_name_or_path are required")
    # 路径解析    
    model_path = resolve_from_file(model_setting, config_path)
    tokenizer_path = resolve_from_file(tokenizer_setting, config_path)
    if not model_path.exists() or not tokenizer_path.exists():
        raise FileNotFoundError(f"local model path does not exist: {model_path}")
    local_only = bool(model_config.get("local_files_only", True))
    trust_remote_code = bool(model_config.get("trust_remote_code", False))
    dtype = _dtype_value(torch, str(model_config.get("torch_dtype", "auto")))
    # 加载模型
    tokenizer, model = _load_model_bundle(
        AutoModelForCausalLM,
        AutoTokenizer,
        model_path,
        tokenizer_path,
        local_only,
        trust_remote_code,
        dtype,
        model_config.get("device_map", "auto"),
        model_config.get("max_memory"),
    )
    # 构建提示
    prompt_messages = _build_prompt_messages(messages, tools_schema)
    # 编码提示
    inputs = tokenizer.apply_chat_template(
        prompt_messages,
        tokenize=True,
        add_generation_prompt=True,
        return_tensors="pt",
        return_dict=True,
        enable_thinking=False,
    )
    # 推理
    device = next(model.parameters()).device
    inputs = inputs.to(device)
    input_length = inputs["input_ids"].shape[-1]
    options = {
        "max_new_tokens": int(generation_config.get("max_new_tokens", 1024)),
        "do_sample": bool(generation_config.get("do_sample", False)),
    }
    with torch.no_grad():
        generated = model.generate(**inputs, **options)
    # 解码输出
    new_tokens = generated[0][input_length:]
    return tokenizer.decode(new_tokens, skip_special_tokens=True)

# 核心函数
def generate_ai_message(
    model_config: str,
    messages: list[dict],
    tools_schema: list[dict],
    artifact_dir: str | None = None,
    artifact_stem: str | None = None,
) -> dict:
    # 加载模型配置
    config_path, config = _load_model_config(model_config)
    messages = validate_messages(deepcopy(messages))
    if not isinstance(tools_schema, list):
        raise ValueError("tools_schema must be an array")
    # 调用真实模型
    generated_at = now_iso()
    backend = config.get("model", {}).get("backend", "transformers")
    raw_text = _prompt_json_generate(config_path, config, messages, tools_schema)
    try:
        parsed_candidate, ai_message = _parse_model_output(raw_text)
        status = "success"
        error = None
    except Exception as exc:
        parsed_candidate = None
        ai_message = make_ai_message(PARSE_ERROR_CONTENT, [])
        status = "error"
        error = {"type": type(exc).__name__, "message": str(exc)}
    raw_record = {
        "mode": "prompt_json",
        "backend": backend,
        "raw_text": raw_text,
        "parsed_candidate": parsed_candidate,
        "status": status,
        "error": error,
        "generated_at": generated_at,
    }
    # 保存结果
    if artifact_dir:
        raw_path, message_path, log_path = _artifact_paths(artifact_dir, artifact_stem)
        write_json(raw_record, raw_path)
        write_json(ai_message, message_path)
        append_jsonl(
            {
                "timestamp": generated_at,
                "mode": "prompt_json",
                "status": status,
                "raw_output_path": str(raw_path),
                "ai_message_path": str(message_path),
                "error": error,
            },
            log_path,
        )
    return {
        "ai_message": ai_message,
        "status": status,
        "error": error,
    }


# Plan-and-Execute 模式 

PARSE_ERROR_CONTENT_PLAN = "模型输出解析失败，无法生成有效执行计划。"


def _load_tools_config_for_planner(tools_config: str | Path) -> tuple[Path, dict]:
    config_path = Path(tools_config).resolve()
    config = read_yaml(config_path)
    if not isinstance(config, dict):
        raise ValueError("tools.yaml must contain an object")
    if not isinstance(config.get("tools"), dict) or not isinstance(config.get("toolsets"), dict):
        raise ValueError("tools.yaml must define tools and toolsets")
    return config_path, config


def _resolve_toolset_for_planner(config: dict, toolset: str | None) -> tuple[str, list[str]]:
    selected = toolset or config.get("default_toolset")
    if not isinstance(selected, str) or selected not in config["toolsets"]:
        raise ValueError(f"toolset does not exist: {selected}")
    names = config["toolsets"][selected]
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise ValueError(f"toolset {selected} must be a list of tool names")
    return selected, names


def _validate_plan_tools(plan: dict, tools_config: str, toolset: str | None) -> None:
    _, config = _load_tools_config_for_planner(tools_config)
    _, allowed_tools = _resolve_toolset_for_planner(config, toolset)
    for step in plan["steps"]:
        tool_name = step["tool_name"]
        if tool_name is None:
            continue
        if tool_name not in allowed_tools:
            raise ValueError(f"plan step references tool not in toolset: {tool_name}")
        if tool_name not in config["tools"]:
            raise ValueError(f"plan step references undefined tool: {tool_name}")


def _parse_plan_output(raw_text: str) -> dict:
    try:
        candidate = json.loads(raw_text.strip())
    except json.JSONDecodeError:
        pattern = r"```json\s*([\s\S]*?)\s*```"
        match = re.search(pattern, raw_text)
        if match:
            candidate = json.loads(match.group(1))
        else:
            raise ValueError("plan output JSON must be a valid JSON object")

    if not isinstance(candidate, dict):
        raise ValueError("plan output JSON must be an object")

    expected_keys = {"plan_id", "total_steps", "plan_summary", "steps"}
    unknown_keys = set(candidate) - expected_keys
    if unknown_keys:
        raise ValueError(f"plan output JSON contains unknown keys: {', '.join(sorted(unknown_keys))}")

    plan = {
        "plan_id": candidate.get("plan_id", ""),
        "total_steps": candidate.get("total_steps", 0),
        "plan_summary": candidate.get("plan_summary", ""),
        "steps": candidate.get("steps", []),
    }

    from common.schemas import validate_execution_plan
    validate_execution_plan(plan)
    return plan


def _build_plan_prompt_messages(
    messages: list[dict],
    tools_schema: list[dict],
    planner_prompt: str,
) -> list[dict]:
    prompt_messages = deepcopy(messages)

    format_instruction = (
        "IMPORTANT OUTPUT FORMAT:\n"
        "You must return exactly one valid JSON object representing an execution plan.\n"
        "Do not output markdown.\n"
        "Do not output explanations.\n"
        'The first output character must be "{" and the last output character must be "}".\n\n'
        "Valid JSON schema:\n"
        "{\n"
        '  "plan_id": "plan_unique_id",\n'
        '  "total_steps": 3,\n'
        '  "plan_summary": "brief summary of the plan",\n'
        '  "steps": [\n'
        '    {\n'
        '      "step_index": 1,\n'
        '      "description": "step description",\n'
        '      "tool_name": "tool_name_or_null",\n'
        '      "tool_args": {"key": "value"} or null,\n'
        '      "reason": "why this step is needed",\n'
        '      "is_final": false\n'
        '    },\n'
        '    {\n'
        '      "step_index": 2,\n'
        '      "description": "final answer",\n'
        '      "tool_name": null,\n'
        '      "tool_args": null,\n'
        '      "reason": "summarize and answer",\n'
        '      "is_final": true\n'
        '    }\n'
        '  ]\n'
        "}\n\n"
        "Key rules:\n"
        "- plan_id must be a unique string identifier\n"
        "- total_steps must match the length of the steps array\n"
        "- Exactly one step must have is_final: true (the last step)\n"
        "- If tool_name is null, tool_args must also be null\n"
        "- description and reason must be non-empty strings\n"
    )

    system_instruction = (
        "\n\n" + planner_prompt + "\n\n"
        "Available tools JSON schema:\n"
        + json.dumps(tools_schema, ensure_ascii=False)
        + "\n"
        + format_instruction
    )

    if prompt_messages and prompt_messages[0].get("role") == "system":
        prompt_messages[0]["content"] += system_instruction
    else:
        prompt_messages.insert(0, {"role": "system", "content": system_instruction.strip()})

    for message in reversed(prompt_messages):
        if message.get("role") == "user":
            message["content"] += "\n\n" + format_instruction
            break

    return prompt_messages


def _prompt_json_generate_plan(
    config_path: Path,
    config: dict,
    messages: list[dict],
    tools_schema: list[dict],
    planner_prompt: str,
) -> str:
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        raise RuntimeError("prompt_json mode requires requirements-llm.txt") from exc

    model_config = config.get("model", {})
    generation_config = config.get("generation", {})
    model_setting = model_config.get("model_name_or_path")
    tokenizer_setting = model_config.get("tokenizer_name_or_path", model_setting)

    if not isinstance(model_setting, str) or not isinstance(tokenizer_setting, str):
        raise ValueError("model_name_or_path and tokenizer_name_or_path are required")

    model_path = resolve_from_file(model_setting, config_path)
    tokenizer_path = resolve_from_file(tokenizer_setting, config_path)

    if not model_path.exists() or not tokenizer_path.exists():
        raise FileNotFoundError(f"local model path does not exist: {model_path}")

    local_only = bool(model_config.get("local_files_only", True))
    trust_remote_code = bool(model_config.get("trust_remote_code", False))
    dtype = _dtype_value(torch, str(model_config.get("torch_dtype", "auto")))

    cache_key = _model_cache_key(
        model_path,
        tokenizer_path,
        local_only,
        trust_remote_code,
        dtype,
        model_config.get("device_map", "auto"),
        model_config.get("max_memory"),
    )

    cached = _MODEL_CACHE.get(cache_key)
    if cached is None:
        tokenizer, model = _load_model_bundle(
            AutoModelForCausalLM,
            AutoTokenizer,
            model_path,
            tokenizer_path,
            local_only,
            trust_remote_code,
            dtype,
            model_config.get("device_map", "auto"),
            model_config.get("max_memory"),
        )
    else:
        tokenizer, model = cached

    prompt_messages = _build_plan_prompt_messages(messages, tools_schema, planner_prompt)

    inputs = tokenizer.apply_chat_template(
        prompt_messages,
        tokenize=True,
        add_generation_prompt=True,
        return_tensors="pt",
        return_dict=True,
        enable_thinking=False,
    )

    device = next(model.parameters()).device
    inputs = inputs.to(device)
    input_length = inputs["input_ids"].shape[-1]

    options = {
        "max_new_tokens": int(generation_config.get("max_new_tokens", 2048)),
        "do_sample": bool(generation_config.get("do_sample", False)),
    }

    with torch.no_grad():
        generated = model.generate(**inputs, **options)

    new_tokens = generated[0][input_length:]
    return tokenizer.decode(new_tokens, skip_special_tokens=True)


def _plan_artifact_paths(artifact_dir: str | Path, stem: str | None) -> tuple[Path, Path, Path]:
    directory = Path(artifact_dir)
    prefix = f"{stem}_" if stem else ""
    return (
        directory / f"{prefix}raw_plan_output.json",
        directory / f"{prefix}plan.json",
        directory / "planner_log.jsonl",
    )


def generate_plan(
    model_config: str,
    messages: list[dict],
    tools_schema: list[dict],
    planner_prompt: str,
    tools_config: str,
    toolset: str | None = None,
    artifact_dir: str | None = None,
    artifact_stem: str | None = None,
) -> dict:
    config_path, config = _load_model_config(model_config)
    messages = deepcopy(messages)

    if not isinstance(tools_schema, list):
        raise ValueError("tools_schema must be an array")

    generated_at = now_iso()

    raw_text = _prompt_json_generate_plan(config_path, config, messages, tools_schema, planner_prompt)
    try:
        plan = _parse_plan_output(raw_text)
        _validate_plan_tools(plan, tools_config, toolset)
        status = "success"
        error = None
    except Exception as exc:
        plan = None
        status = "error"
        error = {"type": type(exc).__name__, "message": str(exc)}

    raw_record = {
        "mode": "prompt_json",
        "raw_text": raw_text,
        "plan": plan,
        "status": status,
        "error": error,
        "generated_at": generated_at,
    }

    if artifact_dir:
        raw_path, plan_path, log_path = _plan_artifact_paths(artifact_dir, artifact_stem)
        write_json(raw_record, raw_path)
        if plan:
            write_json(plan, plan_path)
        append_jsonl(
            {
                "timestamp": generated_at,
                "mode": "prompt_json",
                "status": status,
                "raw_output_path": str(raw_path),
                "plan_path": str(plan_path) if plan else None,
                "error": error,
            },
            log_path,
        )

    return {
        "plan": plan,
        "status": status,
        "error": error,
    }

# 命令行参数解析
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate one AIMessage with a local LLM.")
    parser.add_argument("--model_config", required=True)
    parser.add_argument("--messages", required=True)
    parser.add_argument("--tools_schema", required=True)
    parser.add_argument("--outdir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        outdir = resolve_cli_path(args.outdir)
        generate_ai_message(
            str(resolve_cli_path(args.model_config)),
            read_json(resolve_cli_path(args.messages)),
            read_json(resolve_cli_path(args.tools_schema)),
            str(outdir),
        )
        print(outdir / "ai_message.json")
        return 0
    except Exception as exc:
        print(f"fatal: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())