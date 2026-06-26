from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from time import perf_counter
from typing import Optional

from skills.exceptions import (
    SkillError,
    InputTypeError,
    InputValueError,
    InvalidFormatError,
    ParseError,
)
from skills.error_utils import (
    make_error_result,
    make_success_result,
    validate_type,
    validate_not_empty_string,
    validate_enum_value,
    measure_latency,
)

# 支持的格式
SUPPORTED_FORMATS = {"markdown", "json", "csv", "yaml", "html"}

# 默认配置
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "outputs" / "format_converter_files"
DEFAULT_FILENAMES = {
    "markdown": "converted.md",
    "json": "converted.json",
    "csv": "converted.csv",
    "yaml": "converted.yaml",
    "html": "converted.html",
}
SUFFIXES = {
    "markdown": ".md",
    "json": ".json",
    "csv": ".csv",
    "yaml": ".yaml",
    "html": ".html",
}


def _parse_key_value_lines(text: str) -> dict[str, str]:
    """
    解析key: value格式的文本

    Args:
        text: 输入文本

    Returns:
        解析后的字典

    Raises:
        ParseError: 格式错误时抛出
    """
    result: dict[str, str] = {}
    for line_num, line in enumerate((line.strip() for line in text.splitlines()), 1):
        if not line:
            continue
        if ":" not in line:
            raise ParseError(
                code="FCONV-EXEC-002",
                message=f"第{line_num}行格式错误：缺少冒号",
                details={"line": line_num, "content": line},
                suggestion="请使用 'key: value' 格式"
            )
        key, value = (part.strip() for part in line.split(":", 1))
        if not key:
            raise ParseError(
                code="FCONV-EXEC-002",
                message=f"第{line_num}行key为空",
                details={"line": line_num, "content": line},
                suggestion="请确保每行都有非空的key"
            )
        if key in result:
            raise ParseError(
                code="FCONV-EXEC-003",
                message=f"重复的key：{key}",
                details={"line": line_num, "key": key},
                suggestion="请确保每个key只出现一次"
            )
        result[key] = value

    if not result:
        raise ParseError(
            code="FCONV-EXEC-002",
            message="文本没有可转换的内容",
            details={},
            suggestion="请确保文本包含至少一行 'key: value' 格式的内容"
        )
    return result


def _to_yaml(data: dict | list) -> str:
    """
    将数据转换为YAML格式（简单实现，不依赖PyYAML）

    Args:
        data: 输入数据

    Returns:
        YAML格式字符串
    """
    if isinstance(data, dict):
        lines = []
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{key}:")
                lines.append(_indent(_to_yaml(value), 2))
            elif isinstance(value, str) and ("\n" in value or ":" in value or "#" in value):
                lines.append(f"{key}: |")
                for line in value.split("\n"):
                    lines.append(f"  {line}")
            else:
                lines.append(f"{key}: {value}")
        return "\n".join(lines)
    elif isinstance(data, list):
        lines = []
        for item in data:
            if isinstance(item, (dict, list)):
                lines.append("- ")
                lines.append(_indent(_to_yaml(item), 2))
            else:
                lines.append(f"- {item}")
        return "\n".join(lines)
    else:
        return str(data)


def _indent(text: str, spaces: int) -> str:
    """缩进文本"""
    indent_str = " " * spaces
    return "\n".join(indent_str + line for line in text.split("\n"))


def _to_csv(data: list[dict]) -> str:
    """
    将数据转换为CSV格式

    Args:
        data: 输入数据（字典列表）

    Returns:
        CSV格式字符串
    """
    if not data:
        return ""

    # 收集所有列名
    headers = list(data[0].keys())

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    writer.writerows(data)

    return output.getvalue()


def _to_html_table(data: list[dict]) -> str:
    """
    将数据转换为HTML表格

    Args:
        data: 输入数据（字典列表）

    Returns:
        HTML表格字符串
    """
    if not data:
        return "<table></table>"

    # 收集所有列名
    headers = list(data[0].keys())

    lines = ["<table>"]
    lines.append("  <thead>")
    lines.append("    <tr>")
    for header in headers:
        lines.append(f"      <th>{_escape_html(header)}</th>")
    lines.append("    </tr>")
    lines.append("  </thead>")
    lines.append("  <tbody>")
    for row in data:
        lines.append("    <tr>")
        for header in headers:
            value = row.get(header, "")
            lines.append(f"      <td>{_escape_html(str(value))}</td>")
        lines.append("    </tr>")
    lines.append("  </tbody>")
    lines.append("</table>")

    return "\n".join(lines)


def _escape_html(text: str) -> str:
    """转义HTML特殊字符"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _json_to_key_value(data: dict) -> str:
    """
    将JSON转换为key: value格式

    Args:
        data: 输入字典

    Returns:
        key: value格式字符串
    """
    lines = []
    for key, value in data.items():
        if isinstance(value, (dict, list)):
            value_str = json.dumps(value, ensure_ascii=False)
        else:
            value_str = str(value)
        lines.append(f"{key}: {value_str}")
    return "\n".join(lines)


def _safe_output_path(output_dir: str | None, output_filename: str | None, target_format: str) -> Path:
    """
    生成安全的输出路径

    Args:
        output_dir: 输出目录
        output_filename: 输出文件名
        target_format: 目标格式

    Returns:
        输出文件路径
    """
    directory = Path(output_dir).resolve() if output_dir else DEFAULT_OUTPUT_DIR.resolve()
    raw_name = output_filename.strip() if isinstance(output_filename, str) and output_filename.strip() else DEFAULT_FILENAMES[target_format]
    name = Path(raw_name).name
    suffix = SUFFIXES[target_format]
    path = Path(name)
    stem = path.stem or Path(DEFAULT_FILENAMES[target_format]).stem
    candidate = directory / f"{stem}{suffix}"

    # 如果文件已存在，添加序号
    index = 1
    while candidate.exists():
        candidate = directory / f"{stem}({index}){suffix}"
        index += 1
    return candidate


def _write_output_file(text: str, output_dir: str | None, output_filename: str | None, target_format: str) -> Path:
    """
    写入输出文件

    Args:
        text: 输出文本
        output_dir: 输出目录
        output_filename: 输出文件名
        target_format: 目标格式

    Returns:
        输出文件路径
    """
    target = _safe_output_path(output_dir, output_filename, target_format)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def format_converter(
    text: str,
    target_format: str,
    output_filename: str | None = None,
    output_dir: str | None = None,
    reverse: bool = False,
) -> dict:
    """
    转换文本格式（增强版）

    Args:
        text: 输入文本
        target_format: 目标格式（markdown, json, csv, yaml, html）
        output_filename: 输出文件名（可选）
        output_dir: 输出目录（可选）
        reverse: 是否反向转换（JSON → key-value）

    Returns:
        包含转换结果或错误的字典

    Examples:
        >>> format_converter("name: Agent\nskill: converter", "json")
        {'skill_name': 'format_converter', 'status': 'success', 'input': {...}, 'output': {'formatted_text': '...', 'generated_file_path': '...'}, 'error': None, 'latency_ms': 0.5}

        >>> format_converter('{"name": "Agent"}', "markdown", reverse=True)
        {'skill_name': 'format_converter', 'status': 'success', 'input': {...}, 'output': {'formatted_text': 'name: Agent', ...}, 'error': None, 'latency_ms': 0.3}
    """
    input_data = {
        "text": text,
        "target_format": target_format,
        "output_filename": output_filename,
        "reverse": reverse,
    }

    try:
        with measure_latency() as timer:
            # 验证文本
            validate_not_empty_string(
                text, "text", "format_converter", "FCONV-VAL-002"
            )

            # 验证目标格式
            target = target_format.strip().lower() if isinstance(target_format, str) else ""
            validate_enum_value(
                target, SUPPORTED_FORMATS, "target_format", "format_converter", "FCONV-VAL-003"
            )

            # 解析输入数据
            parsed_data = None
            if target in ("json", "csv", "yaml", "html") or reverse:
                # 尝试解析为JSON
                try:
                    parsed_data = json.loads(text)
                except json.JSONDecodeError:
                    # 尝试解析为key-value格式
                    try:
                        parsed_data = _parse_key_value_lines(text)
                    except ParseError:
                        if target == "json" and not reverse:
                            raise

            # 转换格式
            if target == "markdown":
                if reverse and parsed_data:
                    # JSON → key-value
                    formatted_text = _json_to_key_value(parsed_data)
                else:
                    lines = [line.strip() for line in text.splitlines() if line.strip()]
                    formatted_text = "\n".join(f"- {line}" for line in lines)

            elif target == "json":
                if reverse:
                    # JSON → key-value
                    if parsed_data and isinstance(parsed_data, dict):
                        formatted_text = _json_to_key_value(parsed_data)
                    else:
                        raise ParseError(
                            code="FCONV-EXEC-001",
                            message="反向转换需要有效的JSON对象",
                            details={"input_type": type(parsed_data).__name__},
                            suggestion="请确保输入是有效的JSON对象格式"
                        )
                else:
                    if parsed_data is None:
                        parsed_data = _parse_key_value_lines(text)
                    formatted_text = json.dumps(parsed_data, ensure_ascii=False, indent=2)

            elif target == "csv":
                if parsed_data and isinstance(parsed_data, list):
                    formatted_text = _to_csv(parsed_data)
                elif parsed_data and isinstance(parsed_data, dict):
                    formatted_text = _to_csv([parsed_data])
                else:
                    raise ParseError(
                        code="FCONV-EXEC-001",
                        message="CSV格式需要JSON数组或对象",
                        details={"input_type": type(parsed_data).__name__},
                        suggestion="请确保输入是JSON数组格式，如 [{...}, {...}]"
                    )

            elif target == "yaml":
                if parsed_data:
                    formatted_text = _to_yaml(parsed_data)
                else:
                    raise ParseError(
                        code="FCONV-EXEC-001",
                        message="YAML格式需要有效的JSON或key-value输入",
                        details={},
                        suggestion="请确保输入是有效的JSON或key-value格式"
                    )

            elif target == "html":
                if parsed_data and isinstance(parsed_data, list):
                    formatted_text = _to_html_table(parsed_data)
                elif parsed_data and isinstance(parsed_data, dict):
                    formatted_text = _to_html_table([parsed_data])
                else:
                    raise ParseError(
                        code="FCONV-EXEC-001",
                        message="HTML表格格式需要JSON数组或对象",
                        details={"input_type": type(parsed_data).__name__},
                        suggestion="请确保输入是JSON数组格式，如 [{...}, {...}]"
                    )

            # 写入文件
            generated_path = _write_output_file(formatted_text, output_dir, output_filename, target)

            return make_success_result(
                "format_converter",
                input_data,
                {
                    "formatted_text": formatted_text,
                    "generated_file_path": str(generated_path),
                },
                timer.elapsed_ms
            )

    except SkillError as exc:
        return make_error_result("format_converter", exc, input_data, timer.elapsed_ms if 'timer' in dir() else 0.0)

    except Exception as exc:
        return make_error_result("format_converter", exc, input_data, timer.elapsed_ms if 'timer' in dir() else 0.0)
