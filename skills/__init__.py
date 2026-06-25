from __future__ import annotations

from pathlib import Path

# 导出异常类
from skills.exceptions import (
    SkillError,
    ValidationError,
    InputTypeError,
    InputValueError,
    MissingParameterError,
    InvalidFormatError,
    ExecutionError,
    FileNotFoundError,
    PermissionError,
    ParseError,
    CalculationError,
    EncodingError,
    ResourceError,
    FileSizeError,
    MemoryLimitError,
    TimeoutError,
    ResultOverflowError,
    SecurityError,
    PathEscapeError,
    UnsafeExpressionError,
    RestrictedOperationError,
    UnsafeFunctionError,
)

# 导出工具函数
from skills.error_utils import (
    make_error_result,
    make_success_result,
    validate_type,
    validate_not_empty_string,
    validate_positive_integer,
    validate_non_negative_integer,
    validate_max_length,
    validate_enum_value,
    validate_path_not_escape,
    validate_file_exists,
    validate_directory_exists,
    validate_file_extension,
    measure_latency,
)


DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[1] / "data"


def resolve_data_path(path: str, data_root: str | None = None) -> tuple[Path, Path]:
    """
    解析数据路径

    Args:
        path: 相对于data目录的路径
        data_root: 自定义数据根目录（可选）

    Returns:
        (解析后的绝对路径, 数据根目录)

    Raises:
        PathEscapeError: 路径逃逸时抛出
    """
    root = Path(data_root).resolve() if data_root else DEFAULT_DATA_ROOT.resolve()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PathEscapeError(
            code="PATH-SEC-001",
            message=f"路径逃逸：{path}",
            details={"path": path, "root": str(root)},
            suggestion="请使用相对于数据目录的路径，不要使用 '..' 或绝对路径"
        ) from exc
    return candidate, root
