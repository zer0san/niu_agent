"""Calculator - 安全数学表达式计算器，基于AST白名单"""

from __future__ import annotations

import ast, math, operator

from skills.exceptions import (
    SkillError, CalculationError, ParseError, ResultOverflowError, UnsafeExpressionError,
)
from skills.error_utils import (
    make_error_result, make_success_result, measure_latency,
    validate_not_empty_string, validate_max_length, validate_type,
)

# 运算符
_BINS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
         ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UNS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {"abs": abs, "round": round, "min": min, "max": max, "sum": sum, "int": int, "float": float}
_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf, "nan": math.nan}

MAX_EXPR_LEN, MAX_EXP, MAX_RESULT = 200, 12, 1e100


def _evaluate(node: ast.AST) -> int | float:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in _CONSTS:
            return _CONSTS[node.id]
        raise UnsafeExpressionError(code="CALC-SEC-002", message=f"不安全的变量引用：{node.id}",
                                     details={"variable": node.id})
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNS:
        return _UNS[type(node.op)](_evaluate(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINS:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXP:
            raise CalculationError(code="CALC-EXEC-003", message=f"指数过大（>{MAX_EXP}）",
                                    details={"exponent": right})
        result = _BINS[type(node.op)](left, right)
        if isinstance(result, complex) or not math.isfinite(float(result)) or abs(result) > MAX_RESULT:
            raise ResultOverflowError(code="CALC-EXEC-004", message="计算结果溢出",
                                       details={"result": str(result)})
        return result
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in _FUNCS:
            return _FUNCS[node.func.id](*[_evaluate(a) for a in node.args])
        raise UnsafeExpressionError(code="CALC-SEC-001", message=f"不安全的函数调用：{node.func.id}",
                                     details={"function": node.func.id})
    raise UnsafeExpressionError(code="CALC-EXEC-002", message=f"不支持的表达式：{type(node).__name__}")


def calculator(expression: str) -> dict:
    input_data = {"expression": expression}

    try:
        with measure_latency() as timer:
            validate_type(expression, str, "expression", "calculator", "CALC-VAL-001")
            validate_not_empty_string(expression, "expression", "calculator", "CALC-VAL-002")
            validate_max_length(expression, MAX_EXPR_LEN, "expression", "calculator", "CALC-VAL-003")

            try:
                tree = ast.parse(expression, mode="eval")
            except SyntaxError as exc:
                raise ParseError(code="CALC-EXEC-001", message=f"语法错误：{exc.msg}",
                                 details={"offset": exc.offset}) from exc

            result = _evaluate(tree)

        return make_success_result("calculator", input_data, {"result": result}, timer.elapsed_ms)

    except SkillError as exc:
        return make_error_result("calculator", exc, input_data)
    except Exception as exc:
        return make_error_result("calculator", exc, input_data)
