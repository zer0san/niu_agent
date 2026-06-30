# ==================================================================================
# Skills 错误处理工具函数
# ==================================================================================

from __future__ import annotations

from time import perf_counter
from typing import Any

from skills.exceptions import (
    SkillError,
    InputTypeError,
    InputValueError,
    MissingParameterError,
    InvalidFormatError,
    PathEscapeError,
)


# ==================================================================================
# 错误响应生成
# ==================================================================================

def make_error_result(
    skill_name: str,
    error: Exception,
    input_data: dict,
    latency_ms: float = 0.0
) -> dict:
    """
    生成标准错误响应

    Args:
        skill_name: Skill名称
        error: 异常对象
        input_data: 输入数据
        latency_ms: 执行耗时

    Returns:
        标准错误响应字典
    """
    # 如果是自定义SkillError，使用其错误码
    if isinstance(error, SkillError):
        error_info = error.to_dict()
    else:
        # 否则使用通用错误码
        error_info = {
            "code": f"{skill_name.upper()}-EXEC-UNKNOWN",
            "type": type(error).__name__,
            "message": str(error),
        }

    return {
        "skill_name": skill_name,
        "status": "error",
        "input": input_data,
        "output": None,
        "error": error_info,
        "latency_ms": latency_ms,
    }


def make_success_result(
    skill_name: str,
    input_data: dict,
    output_data: dict,
    latency_ms: float = 0.0
) -> dict:
    """
    生成标准成功响应

    Args:
        skill_name: Skill名称
        input_data: 输入数据
        output_data: 输出数据
        latency_ms: 执行耗时

    Returns:
        标准成功响应字典
    """
    return {
        "skill_name": skill_name,
        "status": "success",
        "input": input_data,
        "output": output_data,
        "error": None,
        "latency_ms": latency_ms,
    }


# ==================================================================================
# 验证工具函数
# ==================================================================================

def validate_type(
    value: Any,
    expected_type: type,
    param_name: str,
    skill_name: str,
    error_code: str
) -> None:
    """
    验证参数类型

    Args:
        value: 待验证的值
        expected_type: 期望的类型
        param_name: 参数名称
        skill_name: Skill名称
        error_code: 错误码

    Raises:
        InputTypeError: 类型不匹配时抛出
    """
    if not isinstance(value, expected_type):
        raise InputTypeError(
            code=error_code,
            message=f"{param_name} 必须是 {expected_type.__name__} 类型",
            details={
                "parameter": param_name,
                "expected_type": expected_type.__name__,
                "actual_type": type(value).__name__,
                "actual_value": repr(value)
            },
            suggestion=f"请确保 {param_name} 是 {expected_type.__name__} 类型"
        )


def validate_not_empty_string(
    value: str,
    param_name: str,
    skill_name: str,
    error_code: str
) -> None:
    """
    验证字符串不为空

    Args:
        value: 待验证的字符串
        param_name: 参数名称
        skill_name: Skill名称
        error_code: 错误码

    Raises:
        InputValueError: 字符串为空时抛出
    """
    if not isinstance(value, str) or not value.strip():
        raise InputValueError(
            code=error_code,
            message=f"{param_name} 必须是非空字符串",
            details={
                "parameter": param_name,
                "actual_value": repr(value)
            },
            suggestion=f"请提供非空的 {param_name}"
        )


def validate_positive_integer(
    value: Any,
    param_name: str,
    skill_name: str,
    error_code: str
) -> None:
    """
    验证正整数

    Args:
        value: 待验证的值
        param_name: 参数名称
        skill_name: Skill名称
        error_code: 错误码

    Raises:
        InputValueError: 不是正整数时抛出
    """
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise InputValueError(
            code=error_code,
            message=f"{param_name} 必须是正整数",
            details={
                "parameter": param_name,
                "actual_value": repr(value)
            },
            suggestion=f"请确保 {param_name} 是大于0的整数"
        )


def validate_non_negative_integer(
    value: Any,
    param_name: str,
    skill_name: str,
    error_code: str
) -> None:
    """
    验证非负整数

    Args:
        value: 待验证的值
        param_name: 参数名称
        skill_name: Skill名称
        error_code: 错误码

    Raises:
        InputValueError: 不是非负整数时抛出
    """
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise InputValueError(
            code=error_code,
            message=f"{param_name} 必须是非负整数",
            details={
                "parameter": param_name,
                "actual_value": repr(value)
            },
            suggestion=f"请确保 {param_name} 是大于等于0的整数"
        )


def validate_max_length(
    value: str,
    max_length: int,
    param_name: str,
    skill_name: str,
    error_code: str
) -> None:
    """
    验证字符串长度

    Args:
        value: 待验证的字符串
        max_length: 最大长度
        param_name: 参数名称
        skill_name: Skill名称
        error_code: 错误码

    Raises:
        InputValueError: 超过最大长度时抛出
    """
    if len(value) > max_length:
        raise InputValueError(
            code=error_code,
            message=f"{param_name} 过长（超过{max_length}字符）",
            details={
                "parameter": param_name,
                "actual_length": len(value),
                "max_length": max_length
            },
            suggestion=f"请将 {param_name} 长度控制在{max_length}字符以内"
        )


def validate_enum_value(
    value: Any,
    allowed_values: set,
    param_name: str,
    skill_name: str,
    error_code: str
) -> None:
    """
    验证枚举值

    Args:
        value: 待验证的值
        allowed_values: 允许的值集合
        param_name: 参数名称
        skill_name: Skill名称
        error_code: 错误码

    Raises:
        InvalidFormatError: 值不在允许范围内时抛出
    """
    if value not in allowed_values:
        raise InvalidFormatError(
            code=error_code,
            message=f"{param_name} 必须是以下之一：{allowed_values}",
            details={
                "parameter": param_name,
                "actual_value": repr(value),
                "allowed_values": list(allowed_values)
            },
            suggestion=f"请将 {param_name} 设置为 {allowed_values} 中的一个"
        )


def validate_path_not_escape(
    path,
    root,
    skill_name: str,
    error_code: str
) -> None:
    """
    验证路径不逃逸

    Args:
        path: 待验证的路径
        root: 根目录
        skill_name: Skill名称
        error_code: 错误码

    Raises:
        PathEscapeError: 路径逃逸时抛出
    """
    try:
        path.relative_to(root)
    except ValueError:
        raise PathEscapeError(
            code=error_code,
            message=f"路径逃逸：{path}",
            details={
                "path": str(path),
                "root": str(root)
            },
            suggestion="请使用相对于数据目录的路径，不要使用 '..' 或绝对路径"
        )


def validate_file_exists(
    path,
    param_name: str,
    skill_name: str,
    error_code: str
) -> None:
    """
    验证文件存在

    Args:
        path: 文件路径
        param_name: 参数名称
        skill_name: Skill名称
        error_code: 错误码

    Raises:
        FileNotFoundError: 文件不存在时抛出
    """
    from skills.exceptions import FileNotFoundError as SkillFileNotFoundError

    if not path.is_file():
        raise SkillFileNotFoundError(
            code=error_code,
            message=f"文件不存在：{path}",
            details={
                "parameter": param_name,
                "path": str(path)
            },
            suggestion=f"请检查 {param_name} 指向的文件是否存在"
        )


def validate_directory_exists(
    path,
    param_name: str,
    skill_name: str,
    error_code: str
) -> None:
    """
    验证目录存在

    Args:
        path: 目录路径
        param_name: 参数名称
        skill_name: Skill名称
        error_code: 错误码

    Raises:
        FileNotFoundError: 目录不存在时抛出
    """
    from skills.exceptions import FileNotFoundError as SkillFileNotFoundError

    if not path.is_dir():
        raise SkillFileNotFoundError(
            code=error_code,
            message=f"目录不存在：{path}",
            details={
                "parameter": param_name,
                "path": str(path)
            },
            suggestion=f"请检查 {param_name} 指向的目录是否存在"
        )


def validate_file_extension(
    path,
    allowed_extensions: set,
    param_name: str,
    skill_name: str,
    error_code: str
) -> None:
    """
    验证文件扩展名

    Args:
        path: 文件路径
        allowed_extensions: 允许的扩展名集合
        param_name: 参数名称
        skill_name: Skill名称
        error_code: 错误码

    Raises:
        InvalidFormatError: 扩展名不允许时抛出
    """
    if path.suffix.lower() not in allowed_extensions:
        raise InvalidFormatError(
            code=error_code,
            message=f"不支持的文件类型：{path.suffix}",
            details={
                "parameter": param_name,
                "actual_extension": path.suffix,
                "allowed_extensions": list(allowed_extensions)
            },
            suggestion=f"请使用以下文件类型：{allowed_extensions}"
        )


# ==================================================================================
# 计时上下文管理器
# ==================================================================================

class measure_latency:
    """
    计时上下文管理器

    使用方式：
        with measure_latency() as timer:
            # 执行操作
            pass
        print(timer.elapsed_ms)
    """

    def __init__(self):
        self.start_time = 0.0
        self.end_time = 0.0
        self.elapsed_ms = 0.0

    def __enter__(self):
        self.start_time = perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end_time = perf_counter()
        self.elapsed_ms = round((self.end_time - self.start_time) * 1000, 3)
        return False