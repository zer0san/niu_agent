from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from time import perf_counter

from skills import resolve_data_path
from skills.exceptions import (
    SkillError,
    InputTypeError,
    InputValueError,
    FileNotFoundError as SkillFileNotFoundError,
    InvalidFormatError,
    PathEscapeError,
    EncodingError,
)
from skills.error_utils import (
    make_error_result,
    make_success_result,
    validate_type,
    validate_positive_integer,
    validate_path_not_escape,
    validate_file_exists,
    validate_file_extension,
    measure_latency,
)


# 支持的文件类型
SUPPORTED_EXTENSIONS = {
    ".txt", ".md",
    ".py", ".json", ".yaml", ".yml", ".csv",
}

# 默认最大字符数
DEFAULT_MAX_CHARS = 2000

# 编码检测顺序
ENCODING_ATTEMPTS = ["utf-8", "gbk", "latin-1", "cp1252"]


def _detect_encoding(file_path: Path) -> str:
    """
    检测文件编码

    Args:
        file_path: 文件路径

    Returns:
        检测到的编码名称
    """
    for encoding in ENCODING_ATTEMPTS:
        try:
            with open(file_path, "r", encoding=encoding) as f:
                f.read(1024)
            return encoding
        except (UnicodeDecodeError, UnicodeError):
            continue
    return "utf-8"


def _get_file_metadata(file_path: Path) -> dict:
    """
    获取文件元数据

    Args:
        file_path: 文件路径

    Returns:
        文件元数据字典
    """
    stat = file_path.stat()
    return {
        "size_bytes": stat.st_size,
        "size_human": _format_size(stat.st_size),
        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "encoding": _detect_encoding(file_path),
    }


def _format_size(size_bytes: int) -> str:
    """
    格式化文件大小

    Args:
        size_bytes: 字节数

    Returns:
        人类可读的文件大小
    """
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


def file_reader(
    path: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    include_metadata: bool = False,
    *,
    data_root: str | None = None
) -> dict:
    """
    读取本地文件

    Args:
        path: 文件路径（相对于data目录）
        max_chars: 最大返回字符数
        include_metadata: 是否包含文件元数据
        data_root: 数据根目录（自动注入）

    Returns:
        包含文件内容或错误的字典
    """
    input_data = {"path": path, "max_chars": max_chars, "include_metadata": include_metadata}

    try:
        with measure_latency() as timer:
            # 验证max_chars
            validate_positive_integer(
                max_chars, "max_chars", "file_reader", "FREAD-VAL-002"
            )

            # 解析路径
            source, root = resolve_data_path(path, data_root)

            # 验证文件类型
            validate_file_extension(
                source, SUPPORTED_EXTENSIONS, "path", "file_reader", "FREAD-VAL-003"
            )

            # 验证文件存在
            validate_file_exists(source, "path", "file_reader", "FREAD-EXEC-001")

            # 读取文件内容
            try:
                encoding = _detect_encoding(source)
                original = source.read_text(encoding=encoding)
            except UnicodeDecodeError as exc:
                raise EncodingError(
                    code="FREAD-EXEC-002",
                    message=f"文件编码错误：{exc}",
                    details={
                        "path": str(source),
                        "encoding": encoding,
                        "error": str(exc)
                    },
                    suggestion="请确保文件使用UTF-8编码"
                ) from exc

            # 截取内容
            content = original[:max_chars]

            # 构建输出
            output = {
                "content": content,
                "num_chars": len(content),
                "source": source.relative_to(root).as_posix(),
                "truncated": len(original) > len(content),
            }

            # 添加元数据（可选）
            if include_metadata:
                output["metadata"] = _get_file_metadata(source)

        return make_success_result(
            "file_reader",
            input_data,
            output,
            timer.elapsed_ms
        )

    except SkillError as exc:
        return make_error_result("file_reader", exc, input_data, timer.elapsed_ms if 'timer' in dir() else 0.0)

    except Exception as exc:
        return make_error_result("file_reader", exc, input_data, timer.elapsed_ms if 'timer' in dir() else 0.0)
