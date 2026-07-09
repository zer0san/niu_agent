from __future__ import annotations

import argparse
from copy import deepcopy
import importlib
import inspect
import json
import sys
from pathlib import Path
from time import perf_counter
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

from common.io_utils import append_jsonl, read_json, read_yaml, write_json
from common.logging_utils import now_iso
from common.path_utils import bootstrap_project_root, resolve_cli_path, resolve_from_file
from common.schemas import make_skill_result, make_tool_message, normalize_tool_call


bootstrap_project_root()


JSON_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": list,
}
RETRYABLE_EXCEPTION_TYPES = {
    "ConnectionError": ConnectionError,
    "FileNotFoundError": FileNotFoundError,
    "OSError": OSError,
    "TimeoutError": TimeoutError,
}
INJECTED_TOOL_PARAMS = {"data_root", "output_dir"}
SKILL_RESULT_KEYS = {"skill_name", "status", "input", "output", "error", "latency_ms"}


def _load_tools_config(tools_config: str | Path) -> tuple[Path, dict]:
    # 读取并做第一层结构校验：B3 后续逻辑都依赖 tools/toolsets 两块配置。
    config_path = Path(tools_config).resolve()
    config = read_yaml(config_path)
    if not isinstance(config, dict):
        raise ValueError("tools.yaml must contain an object")
    if not isinstance(config.get("tools"), dict) or not isinstance(config.get("toolsets"), dict):
        raise ValueError("tools.yaml must define tools and toolsets")
    return config_path, config


def _resolve_toolset(config: dict, toolset: str | None) -> tuple[str, list[str]]:
    # 如果命令行没有指定 toolset，就回退到配置里的 default_toolset。
    selected = toolset or config.get("default_toolset")
    if not isinstance(selected, str) or selected not in config["toolsets"]:
        raise ValueError(f"toolset does not exist: {selected}")
    names = config["toolsets"][selected]
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise ValueError(f"toolset {selected} must be a list of tool names")
    return selected, names


def _parameter_schema(tool: dict) -> dict:
    # 把项目自己的参数配置格式转换为 OpenAI function calling 风格的 JSON Schema。
    raw_parameters = tool.get("parameters", {})
    if not isinstance(raw_parameters, dict):
        raise ValueError("tool parameters must be an object")
    properties = {}
    for name, definition in raw_parameters.items():
        # 每个参数必须声明为 B3 支持的 JSON 基础类型，避免模型拿到不可执行的 schema。
        if not isinstance(definition, dict) or definition.get("type") not in JSON_TYPES:
            raise ValueError(f"invalid parameter schema for {name}")
        properties[name] = dict(definition)
    required = tool.get("required", [])
    if not isinstance(required, list) or not all(name in properties for name in required):
        raise ValueError("required parameters must reference declared properties")
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _annotation_to_schema(annotation: Any) -> dict:
    if annotation is inspect.Signature.empty or annotation is Any:
        return {"type": "string"}
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in {Union, UnionType}:
        non_none_args = [arg for arg in args if arg is not type(None)]
        if len(non_none_args) == 1:
            return _annotation_to_schema(non_none_args[0])
        return {"type": "string"}
    if origin is list:
        schema = {"type": "array"}
        if args:
            schema["items"] = _annotation_to_schema(args[0])
        return schema
    if origin is dict:
        return {"type": "object"}
    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is dict:
        return {"type": "object"}
    if annotation is list:
        return {"type": "array"}
    return {"type": "string"}


def _description_from_docstring(function: Any) -> str:
    doc = inspect.getdoc(function) or ""
    if not doc:
        return f"Call Python function {function.__name__}."
    return doc.splitlines()[0].strip()


def _parameter_schema_from_function(function: Any, configured_tool: dict | None = None) -> dict:
    configured_parameters = {}
    if isinstance(configured_tool, dict) and isinstance(configured_tool.get("parameters"), dict):
        configured_parameters = configured_tool["parameters"]
    type_hints = get_type_hints(function)
    properties = {}
    required = []
    for name, parameter in inspect.signature(function).parameters.items():
        if name in INJECTED_TOOL_PARAMS or parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            continue
        definition = _annotation_to_schema(type_hints.get(name, parameter.annotation))
        configured_definition = configured_parameters.get(name, {})
        if isinstance(configured_definition, dict) and isinstance(configured_definition.get("description"), str):
            definition["description"] = configured_definition["description"]
        else:
            definition["description"] = f"Argument {name} for {function.__name__}."
        if parameter.default is not inspect.Signature.empty:
            default = parameter.default
            if default is None or isinstance(default, (str, int, float, bool, list, dict)):
                definition["default"] = default
        else:
            required.append(name)
        properties[name] = definition
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def get_tools_schema(
    tools_config: str,
    toolset: str,
    outdir: str | None = None,
) -> list[dict]:
    # 对外入口之一：根据 toolset 生成给 LLM 看的工具说明。
    _, config = _load_tools_config(tools_config)
    selected, tool_names = _resolve_toolset(config, toolset)
    schema = []
    for name in tool_names:
        tool = config["tools"].get(name)
        if not isinstance(tool, dict):
            raise ValueError(f"toolset references missing tool: {name}")
        for field in ("module", "function", "description", "returns"):
            if field not in tool:
                raise ValueError(f"tool {name} missing {field}")
        returns = tool["returns"]
        if not isinstance(returns, dict):
            raise ValueError(f"tool {name} returns must be an object")
        # schema 的主体遵循 function calling 格式，x-returns 是本项目额外保留的返回值说明。
        schema.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool["description"],
                    "parameters": _parameter_schema(tool),
                    "x-returns": {"type": "object", "properties": returns},
                },
            }
        )
    if outdir:
        # 导出 schema 的同时写一个简短报告，方便演示或调试时确认选中了哪些工具。
        output_dir = Path(outdir)
        write_json(schema, output_dir / "tools_schema.json")
        write_json(
            {"status": "success", "toolset": selected, "tool_count": len(schema), "tools": tool_names},
            output_dir / "tool_schema_report.json",
        )
    return schema


def get_tools_schema_from_functions(
    tools_config: str,
    toolset: str,
    outdir: str | None = None,
) -> list[dict]:
    # 自动 schema：工具参数来自真实 Python 函数签名，减少 tools.yaml 的手写参数维护。
    _, config = _load_tools_config(tools_config)
    selected, tool_names = _resolve_toolset(config, toolset)
    schema = []
    for name in tool_names:
        tool = config["tools"].get(name)
        if not isinstance(tool, dict):
            raise ValueError(f"toolset references missing tool: {name}")
        for field in ("module", "function"):
            if field not in tool:
                raise ValueError(f"tool {name} missing {field}")
        module = importlib.import_module(tool["module"])
        function = getattr(module, tool["function"])
        returns = tool.get("returns", {"result": {"type": "object", "description": "Function result."}})
        if not isinstance(returns, dict):
            raise ValueError(f"tool {name} returns must be an object")
        description = tool.get("description")
        if not isinstance(description, str) or not description.strip():
            description = _description_from_docstring(function)
        schema.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": _parameter_schema_from_function(function, tool),
                    "x-returns": {"type": "object", "properties": returns},
                    "x-schema-source": "python_signature",
                },
            }
        )
    if outdir:
        output_dir = Path(outdir)
        write_json(schema, output_dir / "tools_schema_auto.json")
        write_json(
            {
                "status": "success",
                "toolset": selected,
                "tool_count": len(schema),
                "tools": tool_names,
                "schema_source": "python_signature",
            },
            output_dir / "tool_schema_auto_report.json",
        )
    return schema


def _validate_args(args: dict, definition: dict) -> None:
    # 校验模型传来的参数：缺必填、多未知字段、类型不匹配都会在这里拦住。
    parameter_schema = _parameter_schema(definition)
    properties = parameter_schema["properties"]
    missing = [name for name in parameter_schema["required"] if name not in args]
    if missing:
        raise ValueError(f"missing required parameters: {', '.join(missing)}")
    unknown = sorted(set(args) - set(properties))
    if unknown:
        raise ValueError(f"unknown parameters: {', '.join(unknown)}")
    for name, value in args.items():
        expected_name = properties[name]["type"]
        expected = JSON_TYPES[expected_name]
        # bool 是 int 的子类；这里显式禁止 True/False 被当成数字参数通过。
        if expected_name in {"integer", "number"} and isinstance(value, bool):
            valid = False
        else:
            valid = isinstance(value, expected)
        if not valid:
            raise ValueError(f"parameter {name} must be {expected_name}")
        if expected_name == "array" and "items" in properties[name]:
            # 数组参数如果声明了 items.type，就继续检查每个元素的类型。
            item_type = properties[name]["items"].get("type")
            if item_type in JSON_TYPES and not all(isinstance(item, JSON_TYPES[item_type]) for item in value):
                raise ValueError(f"parameter {name} contains invalid items")


def _error_result(name: str, args: dict, exc: Exception, latency_ms: float = 0.0) -> dict:
    # 统一把异常包装成 SkillResult，保证上层总能收到结构化错误。
    return make_skill_result(
        name,
        "error",
        args,
        None,
        {"type": type(exc).__name__, "message": str(exc)},
        latency_ms,
    )


def _is_skill_result(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and SKILL_RESULT_KEYS.issubset(value)
        and isinstance(value.get("skill_name"), str)
        and value.get("status") in {"success", "error"}
        and isinstance(value.get("input"), dict)
    )


def _coerce_skill_result(name: str, args: dict, output: Any, latency_ms: float) -> dict:
    if _is_skill_result(output):
        result = deepcopy(output)
        if result.get("latency_ms") is None:
            result["latency_ms"] = latency_ms
        return result
    return make_skill_result(name, "success", args, output, None, latency_ms)


def _tool_cache_key(name: str, args: dict) -> str:
    # 用稳定 JSON 作为缓存键；同名工具、同参数内容才会复用结果。
    return json.dumps({"name": name, "args": args}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _resolve_retry_settings(config: dict) -> tuple[int, tuple[type[BaseException], ...]]:
    settings = config.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("tools settings must be an object")
    max_retries = settings.get("max_retries", 0)
    if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 0:
        raise ValueError("settings.max_retries must be a non-negative integer")
    retryable_names = settings.get("retryable_error_types", [])
    if not isinstance(retryable_names, list) or not all(isinstance(name, str) for name in retryable_names):
        raise ValueError("settings.retryable_error_types must be a list of exception names")
    unknown = sorted(set(retryable_names) - set(RETRYABLE_EXCEPTION_TYPES))
    if unknown:
        raise ValueError(f"unsupported retryable error types: {', '.join(unknown)}")
    return max_retries, tuple(RETRYABLE_EXCEPTION_TYPES[name] for name in retryable_names)


def _is_retryable_error(exc: Exception, retryable_types: tuple[type[BaseException], ...]) -> bool:
    return bool(retryable_types) and isinstance(exc, retryable_types)


def summarize_tool_stats(log_records: list[dict]) -> dict:
    summary = {
        "total_calls": 0,
        "success_count": 0,
        "failure_count": 0,
        "failure_rate": 0.0,
        "average_latency_ms": 0.0,
        "cache_hits": 0,
        "cache_misses": 0,
        "cache_hit_rate": 0.0,
        "execution_attempts": 0,
        "retried_calls": 0,
        "retry_attempts": 0,
        "by_tool": {},
    }
    latency_total = 0.0
    latency_count = 0

    for record in log_records:
        name = record.get("name", "unknown")
        status = record.get("status")
        latency_ms = record.get("latency_ms")
        tool_stats = summary["by_tool"].setdefault(
            name,
            {
                "total_calls": 0,
                "success_count": 0,
                "failure_count": 0,
                "failure_rate": 0.0,
                "average_latency_ms": 0.0,
                "cache_hits": 0,
                "cache_misses": 0,
                "cache_hit_rate": 0.0,
                "execution_attempts": 0,
                "retried_calls": 0,
                "retry_attempts": 0,
                "_latency_total": 0.0,
                "_latency_count": 0,
            },
        )

        summary["total_calls"] += 1
        tool_stats["total_calls"] += 1
        if status == "success":
            summary["success_count"] += 1
            tool_stats["success_count"] += 1
        else:
            summary["failure_count"] += 1
            tool_stats["failure_count"] += 1

        if record.get("cache_eligible"):
            if record.get("cache_hit"):
                summary["cache_hits"] += 1
                tool_stats["cache_hits"] += 1
            else:
                summary["cache_misses"] += 1
                tool_stats["cache_misses"] += 1

        execution_attempts = record.get("execution_attempts", 0)
        retry_count = record.get("retry_count", 0)
        if isinstance(execution_attempts, int) and not isinstance(execution_attempts, bool):
            summary["execution_attempts"] += execution_attempts
            tool_stats["execution_attempts"] += execution_attempts
        if isinstance(retry_count, int) and not isinstance(retry_count, bool):
            summary["retry_attempts"] += retry_count
            tool_stats["retry_attempts"] += retry_count
            if retry_count:
                summary["retried_calls"] += 1
                tool_stats["retried_calls"] += 1

        if isinstance(latency_ms, (int, float)) and not isinstance(latency_ms, bool):
            latency_total += float(latency_ms)
            latency_count += 1
            tool_stats["_latency_total"] += float(latency_ms)
            tool_stats["_latency_count"] += 1

    if summary["total_calls"]:
        summary["failure_rate"] = round(summary["failure_count"] / summary["total_calls"], 4)
    cache_total = summary["cache_hits"] + summary["cache_misses"]
    if cache_total:
        summary["cache_hit_rate"] = round(summary["cache_hits"] / cache_total, 4)
    if latency_count:
        summary["average_latency_ms"] = round(latency_total / latency_count, 3)

    for tool_stats in summary["by_tool"].values():
        total_calls = tool_stats["total_calls"]
        tool_latency_count = tool_stats.pop("_latency_count")
        tool_latency_total = tool_stats.pop("_latency_total")
        if total_calls:
            tool_stats["failure_rate"] = round(tool_stats["failure_count"] / total_calls, 4)
        tool_cache_total = tool_stats["cache_hits"] + tool_stats["cache_misses"]
        if tool_cache_total:
            tool_stats["cache_hit_rate"] = round(tool_stats["cache_hits"] / tool_cache_total, 4)
        if tool_latency_count:
            tool_stats["average_latency_ms"] = round(tool_latency_total / tool_latency_count, 3)
    return summary


def execute_tool_calls(
    tool_calls: list[dict],
    tools_config: str,
    toolset: str | None = None,
    outdir: str | None = None,
) -> list[dict]:
    # 对外入口之二：接收模型产生的 tool_calls，校验、执行，并转换成 ToolMessage。
    config_path, config = _load_tools_config(tools_config)
    selected, allowed_tools = _resolve_toolset(config, toolset)
    max_retries, retryable_error_types = _resolve_retry_settings(config)
    if not isinstance(tool_calls, list):
        raise ValueError("tool_calls must be a list")
    # data_root 写在 tools.yaml 里，并按配置文件所在位置解析，避免受当前工作目录影响。
    data_root_setting = config.get("settings", {}).get("data_root", "../data")
    resolved_data_root = resolve_from_file(data_root_setting, config_path)
    tool_messages = []
    log_records = []
    output_dir = Path(outdir) if outdir else None
    result_cache = {}
    for index, raw_call in enumerate(tool_calls):
        start = perf_counter()
        cache_hit = False
        cache_eligible = False
        execution_attempts = 0
        retry_count = 0
        retry_errors = []
        try:
            # normalize_tool_call 兼容两种输入：标准 function 调用格式和项目内简化格式。
            call = normalize_tool_call(raw_call, index)
        except Exception as exc:
            # 连 tool call 结构都不合法时，仍然生成一条 error ToolMessage，避免流程中断。
            call = {"id": f"call_{index + 1:03d}", "name": "unknown", "args": {}}
            latency_ms = round((perf_counter() - start) * 1000, 3)
            result = _error_result(call["name"], call["args"], exc, latency_ms)
        else:
            name = call["name"]
            args = call["args"]
            if name not in allowed_tools or name not in config["tools"]:
                # 工具必须同时存在于当前 toolset 和 tools 定义中，否则视为不可用。
                latency_ms = round((perf_counter() - start) * 1000, 3)
                result = _error_result(name, args, ValueError(f"tool is not available in {selected}: {name}"), latency_ms)
            else:
                definition = config["tools"][name]
                try:
                    _validate_args(args, definition)
                    cache_eligible = True
                    cache_key = _tool_cache_key(name, args)
                    cached_result = result_cache.get(cache_key)
                    if cached_result is not None:
                        cache_hit = True
                        latency_ms = round((perf_counter() - start) * 1000, 3)
                        result = deepcopy(cached_result)
                        result["latency_ms"] = latency_ms
                    else:
                        # 配置决定要加载哪个 skills 模块、调用哪个函数。
                        module = importlib.import_module(definition["module"])
                        function = getattr(module, definition["function"])
                        kwargs = dict(args)
                        signature = inspect.signature(function)
                        # 只在目标函数声明了对应参数时注入运行环境，保持各 skill 的函数签名灵活。
                        if "data_root" in signature.parameters:
                            kwargs["data_root"] = str(resolved_data_root)
                        if "output_dir" in signature.parameters:
                            kwargs["output_dir"] = str(output_dir) if output_dir else None
                        while True:
                            execution_attempts += 1
                            try:
                                output = function(**kwargs)
                                break
                            except Exception as exc:
                                if execution_attempts <= max_retries and _is_retryable_error(exc, retryable_error_types):
                                    retry_count += 1
                                    retry_errors.append(
                                        {
                                            "attempt": execution_attempts,
                                            "type": type(exc).__name__,
                                            "message": str(exc),
                                        }
                                    )
                                    continue
                                raise
                        latency_ms = round((perf_counter() - start) * 1000, 3)
                        result = _coerce_skill_result(name, args, output, latency_ms)
                        result_cache[cache_key] = deepcopy(result)
                except (ImportError, AttributeError) as exc:
                    raise RuntimeError(f"cannot load configured tool {name}: {exc}") from exc
                except Exception as exc:
                    latency_ms = round((perf_counter() - start) * 1000, 3)
                    result = _error_result(name, args, exc, latency_ms)
        # ToolMessage 的 content 按协议放字符串，所以这里把 SkillResult 压缩成 JSON 字符串。
        content = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        message = make_tool_message(call["id"], call["name"], content, result["status"])
        tool_messages.append(message)
        log_records.append(
            {
                "timestamp": now_iso(),
                "toolset": selected,
                "tool_call_id": call["id"],
                "name": call["name"],
                "status": result["status"],
                "args": call["args"],
                "skill_result": result,
                "latency_ms": result["latency_ms"],
                "cache_eligible": cache_eligible,
                "cache_hit": cache_hit,
                "execution_attempts": execution_attempts,
                "retry_count": retry_count,
                "retried": retry_count > 0,
                "retry_errors": retry_errors,
            }
        )
    if outdir:
        # 本次结果覆盖写入 tool_messages；逐条执行日志追加写入 JSONL，保留历史。
        write_json(tool_messages, output_dir / "tool_messages.json")
        write_json(summarize_tool_stats(log_records), output_dir / "tool_stats.json")
        for record in log_records:
            append_jsonl(record, output_dir / "tool_call_log.jsonl")
    return tool_messages


def build_parser() -> argparse.ArgumentParser:
    # 命令行入口支持两个互斥动作：导出 schema 或执行 tool calls。
    parser = argparse.ArgumentParser(description="Generate tool schema or execute tool calls.")
    parser.add_argument("--tools_config", required=True)
    parser.add_argument("--toolset", default=None)
    parser.add_argument("--tool_calls")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--export_schema", action="store_true")
    action.add_argument("--export_auto_schema", action="store_true")
    action.add_argument("--execute", action="store_true")
    parser.add_argument("--outdir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    # CLI 只做路径解析、分发和致命错误处理；核心逻辑都在上面的可复用函数里。
    args = build_parser().parse_args(argv)
    try:
        config_path = resolve_cli_path(args.tools_config)
        outdir = resolve_cli_path(args.outdir)
        if args.export_schema or args.export_auto_schema:
            if not args.toolset:
                _, config = _load_tools_config(config_path)
                args.toolset = config.get("default_toolset")
            if args.export_auto_schema:
                get_tools_schema_from_functions(str(config_path), args.toolset, str(outdir))
                print(outdir / "tools_schema_auto.json")
            else:
                get_tools_schema(str(config_path), args.toolset, str(outdir))
                print(outdir / "tools_schema.json")
        else:
            if not args.tool_calls:
                raise ValueError("--tool_calls is required with --execute")
            payload = read_json(resolve_cli_path(args.tool_calls))
            tool_calls = payload.get("tool_calls") if isinstance(payload, dict) else payload
            execute_tool_calls(tool_calls, str(config_path), args.toolset, str(outdir))
            print(outdir / "tool_messages.json")
        return 0
    except Exception as exc:
        print(f"fatal: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
