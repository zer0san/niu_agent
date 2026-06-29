from __future__ import annotations

from typing import Any


VALID_ROLES = {"system", "user", "assistant", "tool"}


def make_ai_message(content: str = "", tool_calls: list[dict] | None = None) -> dict:
    message = {
        "role": "assistant",
        "content": content,
        "tool_calls": tool_calls or [],
    }
    validate_ai_message(message)
    return message


def make_tool_message(
    tool_call_id: str,
    name: str,
    content: str,
    status: str = "success",
) -> dict:
    if status not in {"success", "error"}:
        raise ValueError(f"invalid tool status: {status}")
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": name,
        "content": content,
        "status": status,
    }


def make_skill_result(
    skill_name: str,
    status: str,
    input_data: dict,
    output: dict | None = None,
    error: dict | None = None,
    latency_ms: float | None = None,
) -> dict:
    if status not in {"success", "error"}:
        raise ValueError(f"invalid skill status: {status}")
    return {
        "skill_name": skill_name,
        "status": status,
        "input": input_data,
        "output": output,
        "error": error,
        "latency_ms": latency_ms,
    }


def normalize_tool_call(tool_call: dict[str, Any], index: int = 0) -> dict:
    if not isinstance(tool_call, dict):
        raise ValueError("tool call must be an object")
    if "function" in tool_call:
        function = tool_call.get("function") or {}
        name = function.get("name")
        args = function.get("arguments", {})
    else:
        name = tool_call.get("name")
        args = tool_call.get("args", {})
    if isinstance(args, str):
        import json

        args = json.loads(args)
    if not isinstance(name, str) or not name:
        raise ValueError("tool call name must be a non-empty string")
    if not isinstance(args, dict):
        raise ValueError("tool call args must be an object")
    call_id = tool_call.get("id") or f"call_{index + 1:03d}"
    if not isinstance(call_id, str) or not call_id:
        raise ValueError("tool call id must be a non-empty string")
    return {"id": call_id, "name": name, "args": args}


def validate_ai_message(message: dict) -> None:
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise ValueError("AIMessage role must be assistant")
    if not isinstance(message.get("content"), str):
        raise ValueError("AIMessage content must be a string")
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        raise ValueError("AIMessage tool_calls must be a list")
    normalized = [normalize_tool_call(call, index) for index, call in enumerate(tool_calls)]
    message["tool_calls"] = normalized
    if not message["content"] and not normalized:
        raise ValueError("AIMessage must contain content or tool_calls")


def validate_messages(messages: Any) -> list[dict]:
    if not isinstance(messages, list):
        raise ValueError("messages must be a top-level array")
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ValueError(f"message {index} must be an object")
        role = message.get("role")
        if role not in VALID_ROLES:
            raise ValueError(f"message {index} has invalid role: {role}")
        if not isinstance(message.get("content", ""), str):
            raise ValueError(f"message {index} content must be a string")
        if role == "assistant":
            message.setdefault("tool_calls", [])
            validate_ai_message(message)
        if role == "tool":
            for field in ("tool_call_id", "name", "status"):
                if field not in message:
                    raise ValueError(f"tool message {index} missing {field}")
    return messages


def make_plan_step(
    step_index: int,
    description: str,
    tool_name: str | None,
    tool_args: dict | None,
    reason: str,
    is_final: bool = False,
) -> dict:
    step = {
        "step_index": step_index,
        "description": description,
        "tool_name": tool_name,
        "tool_args": tool_args,
        "reason": reason,
        "is_final": is_final,
    }
    validate_plan_step(step)
    return step


def validate_plan_step(step: dict) -> None:
    if not isinstance(step, dict):
        raise ValueError("plan step must be an object")
    if not isinstance(step.get("step_index"), int) or step["step_index"] < 1:
        raise ValueError("plan step_index must be a positive integer")
    if not isinstance(step.get("description"), str) or not step["description"].strip():
        raise ValueError("plan step description must be a non-empty string")
    tool_name = step.get("tool_name")
    tool_args = step.get("tool_args")
    if tool_name is not None:
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("plan step tool_name must be a non-empty string or null")
        if not isinstance(tool_args, dict):
            raise ValueError("plan step tool_args must be an object when tool_name is provided")
    else:
        if tool_args is not None:
            raise ValueError("plan step tool_args must be null when tool_name is null")
    if not isinstance(step.get("reason"), str) or not step["reason"].strip():
        raise ValueError("plan step reason must be a non-empty string")
    if not isinstance(step.get("is_final"), bool):
        raise ValueError("plan step is_final must be boolean")


def make_execution_plan(
    plan_id: str,
    total_steps: int,
    plan_summary: str,
    steps: list[dict],
) -> dict:
    plan = {
        "plan_id": plan_id,
        "total_steps": total_steps,
        "plan_summary": plan_summary,
        "steps": steps,
    }
    validate_execution_plan(plan)
    return plan


def validate_execution_plan(plan: dict) -> None:
    if not isinstance(plan, dict):
        raise ValueError("execution plan must be an object")
    if not isinstance(plan.get("plan_id"), str) or not plan["plan_id"].strip():
        raise ValueError("execution plan plan_id must be a non-empty string")
    if not isinstance(plan.get("total_steps"), int) or plan["total_steps"] < 1:
        raise ValueError("execution plan total_steps must be a positive integer")
    if not isinstance(plan.get("plan_summary"), str) or not plan["plan_summary"].strip():
        raise ValueError("execution plan plan_summary must be a non-empty string")
    steps = plan.get("steps")
    if not isinstance(steps, list) or len(steps) == 0:
        raise ValueError("execution plan steps must be a non-empty array")
    if len(steps) != plan["total_steps"]:
        raise ValueError("execution plan total_steps must match steps array length")
    seen_indices = set()
    final_count = 0
    for index, step in enumerate(steps):
        validate_plan_step(step)
        if step["step_index"] in seen_indices:
            raise ValueError(f"duplicate step_index in plan: {step['step_index']}")
        seen_indices.add(step["step_index"])
        if step["step_index"] != index + 1:
            raise ValueError(f"plan steps must be sequentially numbered, got {step['step_index']} at position {index + 1}")
        if step["is_final"]:
            final_count += 1
    if final_count != 1:
        raise ValueError("execution plan must contain exactly one final step")
