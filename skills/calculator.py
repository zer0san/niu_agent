from __future__ import annotations

import ast
import math
import operator
import json
from time import perf_counter

from skills.exceptions import (
    SkillError,
    InputTypeError,
    InputValueError,
    ParseError,
    CalculationError,
    UnsafeExpressionError,
    ResultOverflowError,
)
from skills.error_utils import (
    make_error_result,
    make_success_result,
    validate_type,
    validate_not_empty_string,
    validate_max_length,
    measure_latency,
)


# 支持的二元运算符
_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

# 支持的一元运算符
_UNARY_OPERATORS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# 安全的数学函数白名单
_SAFE_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "int": int,
    "float": float,
}

# 安全的数学常量
_SAFE_CONSTANTS = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "inf": math.inf,
    "nan": math.nan,
}

# 限制配置
MAX_EXPRESSION_LENGTH = 200
MAX_EXPONENT_MAGNITUDE = 12
MAX_RESULT_MAGNITUDE = 1e100


def _evaluate(node: ast.AST) -> int | float:
    """
    递归计算AST节点

    Args:
        node: AST节点

    Returns:
        计算结果

    Raises:
        CalculationError: 计算错误
        UnsafeExpressionError: 不安全的表达式
        ResultOverflowError: 结果溢出
    """
    # 处理Expression节点
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)

    # 处理数字常量
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value

    # 处理名称引用（数学常量）
    if isinstance(node, ast.Name):
        name = node.id
        if name in _SAFE_CONSTANTS:
            return _SAFE_CONSTANTS[name]
        raise UnsafeExpressionError(
            code="CALC-SEC-002",
            message=f"不安全的变量引用：{name}",
            details={"variable": name},
            suggestion=f"只支持以下常量：{list(_SAFE_CONSTANTS.keys())}"
        )

    # 处理一元运算符
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        return _UNARY_OPERATORS[type(node.op)](_evaluate(node.operand))

    # 处理二元运算符
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _evaluate(node.left)
        right = _evaluate(node.right)

        # 检查指数大小
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT_MAGNITUDE:
            raise CalculationError(
                code="CALC-EXEC-003",
                message=f"指数过大（超过{MAX_EXPONENT_MAGNITUDE}）",
                details={
                    "exponent": right,
                    "max_exponent": MAX_EXPONENT_MAGNITUDE
                },
                suggestion=f"请将指数控制在±{MAX_EXPONENT_MAGNITUDE}以内"
            )

        result = _BINARY_OPERATORS[type(node.op)](left, right)

        # 检查结果范围
        if isinstance(result, complex):
            raise ResultOverflowError(
                code="CALC-EXEC-004",
                message="计算结果为复数",
                details={"result": str(result)},
                suggestion="请避免产生复数的运算（如负数开偶次方）"
            )

        if not math.isfinite(float(result)):
            raise ResultOverflowError(
                code="CALC-EXEC-004",
                message="计算结果为无穷大或NaN",
                details={"result": str(result)},
                suggestion="请检查是否有除以0或溢出的运算"
            )

        if abs(result) > MAX_RESULT_MAGNITUDE:
            raise ResultOverflowError(
                code="CALC-EXEC-004",
                message=f"计算结果超出范围（超过{MAX_RESULT_MAGNITUDE}）",
                details={
                    "result": result,
                    "max_magnitude": MAX_RESULT_MAGNITUDE
                },
                suggestion="请减小运算数值"
            )

        return result

    # 处理函数调用
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        func_name = node.func.id
        if func_name in _SAFE_FUNCTIONS:
            args = [_evaluate(arg) for arg in node.args]
            return _SAFE_FUNCTIONS[func_name](*args)
        raise UnsafeExpressionError(
            code="CALC-SEC-001",
            message=f"不安全的函数调用：{func_name}",
            details={"function": func_name},
            suggestion=f"只支持以下函数：{list(_SAFE_FUNCTIONS.keys())}"
        )

    # 不支持的表达式元素
    raise UnsafeExpressionError(
        code="CALC-EXEC-002",
        message=f"不支持的表达式元素：{type(node).__name__}",
        details={"node_type": type(node).__name__},
        suggestion="请使用数字、运算符和白名单函数"
    )


def calculator(expression: str) -> dict:
    """
    计算数学表达式

    Args:
        expression: 数学表达式字符串

    Returns:
        包含结果或错误的字典
    """
    input_data = {"expression": expression}

    try:
        with measure_latency() as timer:
            # 验证输入类型
            validate_type(
                expression, str, "expression", "calculator", "CALC-VAL-001"
            )

            # 验证不为空
            validate_not_empty_string(
                expression, "expression", "calculator", "CALC-VAL-002"
            )

            # 验证长度
            validate_max_length(
                expression, MAX_EXPRESSION_LENGTH, "expression", "calculator", "CALC-VAL-003"
            )

            # 解析表达式
            try:
                tree = ast.parse(expression, mode="eval")
            except SyntaxError as exc:
                raise ParseError(
                    code="CALC-EXEC-001",
                    message=f"表达式语法错误：{exc.msg}",
                    details={
                        "position": exc.offset,
                        "line": exc.lineno,
                        "text": exc.text
                    },
                    suggestion="请检查表达式语法是否正确"
                ) from exc

            # 计算结果
            result = _evaluate(tree)

        return make_success_result(
            "calculator",
            input_data,
            {"result": result},
            timer.elapsed_ms
        )

    except SkillError as exc:
        return make_error_result("calculator", exc, input_data, timer.elapsed_ms if 'timer' in dir() else 0.0)

    except Exception as exc:
        return make_error_result("calculator", exc, input_data, timer.elapsed_ms if 'timer' in dir() else 0.0)
