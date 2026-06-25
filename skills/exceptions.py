# ==================================================================================
# Skills 异常类定义
# ==================================================================================

from __future__ import annotations


class SkillError(Exception):
    """Skill错误基类"""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict = None,
        suggestion: str = None,
        documentation: str = None
    ):
        self.code = code
        self.message = message
        self.details = details or {}
        self.suggestion = suggestion
        self.documentation = documentation
        super().__init__(message)

    def to_dict(self) -> dict:
        """转换为字典格式"""
        result = {
            "code": self.code,
            "type": self.__class__.__name__,
            "message": self.message,
        }
        if self.details:
            result["details"] = self.details
        if self.suggestion:
            result["suggestion"] = self.suggestion
        if self.documentation:
            result["documentation"] = self.documentation
        return result


# ==================================================================================
# 验证错误 (ValidationError)
# ==================================================================================

class ValidationError(SkillError):
    """验证错误基类"""
    pass


class InputTypeError(ValidationError):
    """输入类型错误"""
    pass


class InputValueError(ValidationError):
    """输入值错误"""
    pass


class MissingParameterError(ValidationError):
    """缺少必填参数"""
    pass


class InvalidFormatError(ValidationError):
    """格式无效"""
    pass


# ==================================================================================
# 执行错误 (ExecutionError)
# ==================================================================================

class ExecutionError(SkillError):
    """执行错误基类"""
    pass


class FileNotFoundError(ExecutionError):
    """文件不存在"""
    pass


class PermissionError(ExecutionError):
    """权限不足"""
    pass


class ParseError(ExecutionError):
    """解析失败"""
    pass


class CalculationError(ExecutionError):
    """计算错误"""
    pass


class EncodingError(ExecutionError):
    """编码错误"""
    pass


# ==================================================================================
# 资源错误 (ResourceError)
# ==================================================================================

class ResourceError(SkillError):
    """资源错误基类"""
    pass


class FileSizeError(ResourceError):
    """文件大小超限"""
    pass


class MemoryLimitError(ResourceError):
    """内存超限"""
    pass


class TimeoutError(ResourceError):
    """执行超时"""
    pass


class ResultOverflowError(ResourceError):
    """结果溢出"""
    pass


# ==================================================================================
# 安全错误 (SecurityError)
# ==================================================================================

class SecurityError(SkillError):
    """安全错误基类"""
    pass


class PathEscapeError(SecurityError):
    """路径逃逸"""
    pass


class UnsafeExpressionError(SecurityError):
    """不安全表达式"""
    pass


class RestrictedOperationError(SecurityError):
    """受限操作"""
    pass


class UnsafeFunctionError(SecurityError):
    """不安全函数调用"""
    pass