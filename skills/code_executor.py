"""
Code Executor Skill - 安全的沙箱代码执行器

安全限制：
- 禁止import语句
- 禁止危险内置函数
- 禁止属性访问攻击
- 限制递归深度
- 限制输出大小
- 限制变量数量
- 限制代码长度
- 限制执行时间
"""

from __future__ import annotations

import ast
import io
import sys
import traceback
from contextlib import redirect_stdout, redirect_stderr
from time import perf_counter
from typing import Any

from skills.exceptions import (
    SkillError,
    InputTypeError,
    InputValueError,
    ParseError,
    ExecutionError,
    SecurityError,
    TimeoutError,
)
from skills.error_utils import (
    make_error_result,
    make_success_result,
    validate_type,
    validate_not_empty_string,
    validate_max_length,
    measure_latency,
)


# ==================================================================================
# 沙箱配置
# ==================================================================================

# 代码限制
MAX_CODE_LENGTH = 10000          # 最大代码长度
MAX_EXECUTION_TIME = 5           # 最大执行时间（秒）
MAX_RECURSION_DEPTH = 100        # 最大递归深度
MAX_OUTPUT_SIZE = 100000         # 最大输出大小（字符）
MAX_VARIABLES = 100              # 最大变量数量
MAX_ITERATIONS = 100000          # 最大循环迭代次数
MAX_STRING_LENGTH = 10000        # 最大字符串长度
MAX_LIST_LENGTH = 10000          # 最大列表/元组长度
MAX_DICT_SIZE = 1000             # 最大字典大小

# 禁止的属性访问（防止内省攻击）
FORBIDDEN_ATTRIBUTES = {
    '__class__',
    '__bases__',
    '__subclasses__',
    '__mro__',
    '__globals__',
    '__locals__',
    '__builtins__',
    '__import__',
    '__loader__',
    '__spec__',
    '__file__',
    '__name__',
    '__dict__',
    '__doc__',
    '__delattr__',
    '__dir__',
    '__format__',
    '__getattribute__',
    '__hash__',
    '__init__',
    '__init_subclass__',
    '__new__',
    '__reduce__',
    '__reduce_ex__',
    '__repr__',
    '__setattr__',
    '__sizeof__',
    '__str__',
    '__subclasshook__',
}

# 禁止的AST节点类型
FORBIDDEN_AST_NODES = {
    ast.Global,
    ast.Nonlocal,
}

if hasattr(ast, 'Exec'):
    FORBIDDEN_AST_NODES.add(ast.Exec)
if hasattr(ast, 'Print'):
    FORBIDDEN_AST_NODES.add(ast.Print)

# 禁止的内置函数
FORBIDDEN_BUILTINS = {
    'exec',
    'eval',
    'compile',
    '__import__',
    'open',
    'input',
    'breakpoint',
    'exit',
    'quit',
    'help',
    'license',
    'credits',
    'copyright',
    'getattr',
    'setattr',
    'delattr',
    'globals',
    'locals',
    'vars',
    'dir',
    'type',
    'id',
    'hash',
    'callable',
    'isinstance',
    'issubclass',
    'super',
    'memoryview',
    'bytearray',
    'bytes',
}

# 禁止的模块
FORBIDDEN_MODULES = {
    'os',
    'sys',
    'subprocess',
    'shutil',
    'socket',
    'http',
    'urllib',
    'requests',
    'ctypes',
    'importlib',
    'pkgutil',
    'inspect',
    'code',
    'codeop',
    'compile',
    'ast',
    'dis',
    'pickle',
    'shelve',
    'marshal',
    'dbm',
    'sqlite3',
    'xml',
    'html',
    'email',
    'logging',
    'threading',
    'multiprocessing',
    'concurrent',
    'asyncio',
    'signal',
    'mmap',
    'fcntl',
    'termios',
    'tty',
    'pty',
    'select',
    'selectors',
    'asyncore',
    'asynchat',
    'ssl',
    'hashlib',
    'hmac',
    'secrets',
    'base64',
    'binascii',
    'quopri',
    'uu',
}


# ==================================================================================
# 沙箱执行器
# ==================================================================================

class SandboxRestricted:
    """沙箱限制包装器"""

    def __init__(self, max_iterations=MAX_ITERATIONS):
        self.iterations = 0
        self.max_iterations = max_iterations

    def check_iteration(self):
        """检查循环迭代次数"""
        self.iterations += 1
        if self.iterations > self.max_iterations:
            raise ExecutionError(
                code="CODE-SEC-004",
                message=f"循环迭代次数超过限制（{self.max_iterations}次）",
                details={"max_iterations": self.max_iterations},
                suggestion="请减少循环次数或优化算法"
            )


def _validate_code_safety(code: str) -> None:
    """
    验证代码安全性（AST级别）

    Args:
        code: Python代码

    Raises:
        SecurityError: 代码不安全时抛出
        ParseError: 语法错误时抛出
    """
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise ParseError(
            code="CODE-EXEC-001",
            message=f"代码语法错误：{exc.msg}",
            details={
                "line": exc.lineno,
                "offset": exc.offset,
                "text": exc.text
            },
            suggestion="请检查代码语法"
        ) from exc

    # 允许的白名单模块
    SAFE_IMPORT_MODULES = {
        'math', 'random', 'string', 'collections', 'itertools',
        'functools', 'operator', 'json', 're', 'datetime',
        'decimal', 'fractions', 'statistics', 'copy',
    }

    def _is_safe_import(node):
        """检查是否是安全的import"""
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split('.')[0] not in SAFE_IMPORT_MODULES:
                    return False
            return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split('.')[0] in SAFE_IMPORT_MODULES:
                return True
            return False
        return False

    for node in ast.walk(tree):
        # 检查禁止的AST节点
        if type(node) in FORBIDDEN_AST_NODES:
            raise SecurityError(
                code="CODE-SEC-001",
                message=f"禁止的代码结构：{type(node).__name__}",
                details={"node_type": type(node).__name__},
                suggestion="不允许使用global等语句"
            )

        # 检查import语句
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if not _is_safe_import(node):
                module_name = node.names[0].name if isinstance(node, ast.Import) else node.module
                raise SecurityError(
                    code="CODE-SEC-001",
                    message=f"禁止的import：{module_name}",
                    details={"module": module_name},
                    suggestion=f"只允许导入以下模块：{SAFE_IMPORT_MODULES}"
                )

        # 检查函数调用
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in FORBIDDEN_BUILTINS:
                    raise SecurityError(
                        code="CODE-SEC-002",
                        message=f"禁止的函数调用：{node.func.id}",
                        details={"function": node.func.id},
                        suggestion=f"不允许调用 {node.func.id} 函数"
                    )

            if isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name):
                    if node.func.value.id in FORBIDDEN_MODULES:
                        raise SecurityError(
                            code="CODE-SEC-003",
                            message=f"禁止的模块调用：{node.func.value.id}.{node.func.attr}",
                            details={"module": node.func.value.id, "function": node.func.attr},
                            suggestion="不允许调用系统相关模块"
                        )

        # 检查属性访问（防止内省攻击）
        if isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_ATTRIBUTES:
                raise SecurityError(
                    code="CODE-SEC-005",
                    message=f"禁止的属性访问：{node.attr}",
                    details={"attribute": node.attr},
                    suggestion="不允许访问类内部属性"
                )

        # 检查下标访问（防止 __builtins__[...] 攻击）
        if isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name) and node.value.id in ('__builtins__', '__globals__', '__locals__'):
                raise SecurityError(
                    code="CODE-SEC-006",
                    message=f"禁止的下标访问：{node.value.id}[...]",
                    details={"target": node.value.id},
                    suggestion="不允许通过下标访问内置模块"
                )


def _create_safe_globals() -> dict:
    """
    创建安全的执行环境

    Returns:
        安全的全局变量字典
    """
    import math
    import random
    import string
    import collections
    import itertools
    import functools
    import operator
    import json
    import re
    import datetime
    import decimal
    import fractions
    import statistics
    import copy

    # 安全的内置函数
    safe_builtins = {
        # 类型转换
        'abs': abs,
        'all': all,
        'any': any,
        'bool': bool,
        'chr': chr,
        'divmod': divmod,
        'float': float,
        'format': format,
        'frozenset': frozenset,
        'int': int,
        'iter': iter,
        'len': len,
        'list': list,
        'map': map,
        'max': max,
        'min': min,
        'next': next,
        'oct': oct,
        'ord': ord,
        'pow': pow,
        'print': print,
        'range': range,
        'repr': repr,
        'reversed': reversed,
        'round': round,
        'set': set,
        'slice': slice,
        'sorted': sorted,
        'str': str,
        'sum': sum,
        'tuple': tuple,
        'zip': zip,
        'enumerate': enumerate,
        'filter': filter,
        'dict': dict,
        'hex': hex,
        'bin': bin,
        'complex': complex,
        'hash': hash,
        'object': object,
        'property': property,
        'staticmethod': staticmethod,
        'classmethod': classmethod,
        'hasattr': hasattr,
        'callable': callable,
        'isinstance': isinstance,
        'issubclass': issubclass,
    }

    # 安全的__import__函数（只允许白名单模块）
    SAFE_IMPORT_MODULES = {
        'math', 'random', 'string', 'collections', 'itertools',
        'functools', 'operator', 'json', 're', 'datetime',
        'decimal', 'fractions', 'statistics', 'copy',
    }

    original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

    def safe_import(name, *args, **kwargs):
        module_root = name.split('.')[0]
        if module_root not in SAFE_IMPORT_MODULES:
            raise ImportError(f"不允许导入模块：{name}")
        return original_import(name, *args, **kwargs)

    safe_builtins['__import__'] = safe_import

    # 安全的模块
    safe_modules = {
        'math': math,
        'random': random,
        'string': string,
        'collections': collections,
        'itertools': itertools,
        'functools': functools,
        'operator': operator,
        'json': json,
        're': re,
        'datetime': datetime,
        'decimal': decimal,
        'fractions': fractions,
        'statistics': statistics,
        'copy': copy,
    }

    # 数学常量
    safe_constants = {
        'pi': math.pi,
        'e': math.e,
        'tau': math.tau,
        'inf': math.inf,
        'nan': math.nan,
    }

    # 合并所有安全的全局变量
    safe_globals = {'__builtins__': safe_builtins}
    safe_globals.update(safe_modules)
    safe_globals.update(safe_constants)

    return safe_globals


def _create_limited_globals(sandbox: SandboxRestricted) -> dict:
    """
    创建带限制的全局变量环境

    Args:
        sandbox: 沙箱限制对象

    Returns:
        带限制的全局变量字典
    """
    safe_globals = _create_safe_globals()

    # 添加受限的range函数
    original_range = range
    def limited_range(*args):
        if len(args) == 1:
            stop = args[0]
            if stop > MAX_ITERATIONS:
                raise ExecutionError(
                    code="CODE-SEC-004",
                    message=f"range({stop})超过最大迭代限制{MAX_ITERATIONS}",
                    details={"stop": stop, "max": MAX_ITERATIONS},
                    suggestion=f"请将range参数限制在{MAX_ITERATIONS}以内"
                )
        elif len(args) == 2:
            start, stop = args
            if stop - start > MAX_ITERATIONS:
                raise ExecutionError(
                    code="CODE-SEC-004",
                    message=f"range({start}, {stop})超过最大迭代限制{MAX_ITERATIONS}",
                    details={"start": start, "stop": stop, "max": MAX_ITERATIONS},
                    suggestion=f"请将range范围限制在{MAX_ITERATIONS}以内"
                )
        elif len(args) == 3:
            start, stop, step = args
            if step != 0 and abs((stop - start) / step) > MAX_ITERATIONS:
                raise ExecutionError(
                    code="CODE-SEC-004",
                    message=f"range({start}, {stop}, {step})超过最大迭代限制{MAX_ITERATIONS}",
                    details={"start": start, "stop": stop, "step": step, "max": MAX_ITERATIONS},
                    suggestion=f"请将range范围限制在{MAX_ITERATIONS}以内"
                )
        return original_range(*args)

    safe_globals['__builtins__']['range'] = limited_range

    # 添加受限的字符串操作
    class SafeString(str):
        def __add__(self, other):
            result = super().__add__(other)
            if len(result) > MAX_STRING_LENGTH:
                raise ExecutionError(
                    code="CODE-SEC-007",
                    message=f"字符串长度超过限制{MAX_STRING_LENGTH}",
                    details={"length": len(result), "max": MAX_STRING_LENGTH},
                    suggestion="请减少字符串长度"
                )
            return result

    # 添加受限的列表操作
    class SafeList(list):
        def append(self, item):
            if len(self) >= MAX_LIST_LENGTH:
                raise ExecutionError(
                    code="CODE-SEC-008",
                    message=f"列表长度超过限制{MAX_LIST_LENGTH}",
                    details={"length": len(self), "max": MAX_LIST_LENGTH},
                    suggestion="请减少列表长度"
                )
            super().append(item)

    safe_globals['__builtins__']['str'] = SafeString
    safe_globals['__builtins__']['list'] = SafeList

    return safe_globals


def code_executor(
    code: str,
    timeout: int = MAX_EXECUTION_TIME,
    capture_print: bool = True,
) -> dict:
    """
    执行Python代码（安全沙箱）

    Args:
        code: Python代码字符串
        timeout: 最大执行时间（秒）
        capture_print: 是否捕获print输出

    Returns:
        包含执行结果或错误的字典
    """
    input_data = {
        'code': code[:100] + '...' if isinstance(code, str) and len(code) > 100 else code,
        'timeout': timeout,
        'capture_print': capture_print,
    }

    try:
        with measure_latency() as timer:
            # 验证代码不为空
            validate_not_empty_string(
                code, 'code', 'code_executor', 'CODE-VAL-001'
            )

            # 验证代码长度
            validate_max_length(
                code, MAX_CODE_LENGTH, 'code', 'code_executor', 'CODE-VAL-002'
            )

            # 验证超时参数
            if not isinstance(timeout, int) or timeout < 1 or timeout > 30:
                raise InputValueError(
                    code='CODE-VAL-003',
                    message='timeout必须是1-30之间的整数',
                    details={'timeout': timeout},
                    suggestion='请将timeout设置为1-30之间的整数'
                )

            # 验证代码安全性（AST级别）
            _validate_code_safety(code)

            # 创建沙箱限制对象
            sandbox = SandboxRestricted(max_iterations=MAX_ITERATIONS)

            # 创建安全的执行环境
            safe_globals = _create_limited_globals(sandbox)
            safe_locals = {}

            # 捕获输出
            stdout_capture = io.StringIO()
            stderr_capture = io.StringIO()

            # 设置递归深度限制
            old_limit = sys.getrecursionlimit()
            sys.setrecursionlimit(MAX_RECURSION_DEPTH)

            try:
                with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                    # 编译代码
                    compiled_code = compile(code, '<input>', 'exec')

                    # 执行代码
                    exec(compiled_code, safe_globals, safe_locals)

            except RecursionError:
                raise ExecutionError(
                    code='CODE-SEC-009',
                    message=f'递归深度超过限制{MAX_RECURSION_DEPTH}',
                    details={'max_depth': MAX_RECURSION_DEPTH},
                    suggestion='请减少递归深度或使用迭代实现'
                )
            except ExecutionError:
                raise
            except Exception as exc:
                error_trace = traceback.format_exc()
                raise ExecutionError(
                    code='CODE-EXEC-003',
                    message=f'代码执行错误：{str(exc)}',
                    details={
                        'error_type': type(exc).__name__,
                        'error_message': str(exc),
                        'traceback': error_trace
                    },
                    suggestion='请检查代码逻辑'
                ) from exc
            finally:
                # 恢复递归深度限制
                sys.setrecursionlimit(old_limit)

            # 获取输出
            stdout_output = stdout_capture.getvalue()
            stderr_output = stderr_capture.getvalue()

            # 检查输出大小
            if len(stdout_output) > MAX_OUTPUT_SIZE:
                stdout_output = stdout_output[:MAX_OUTPUT_SIZE] + f'\n... (输出被截断，超过{MAX_OUTPUT_SIZE}字符)'
            if len(stderr_output) > MAX_OUTPUT_SIZE:
                stderr_output = stderr_output[:MAX_OUTPUT_SIZE] + f'\n... (输出被截断，超过{MAX_OUTPUT_SIZE}字符)'

            # 获取变量（过滤内部变量）
            variables = {}
            for k, v in safe_locals.items():
                if not k.startswith('_') and len(variables) < MAX_VARIABLES:
                    # 检查变量值大小
                    try:
                        repr_v = repr(v)
                        if len(repr_v) > MAX_STRING_LENGTH:
                            variables[k] = repr_v[:MAX_STRING_LENGTH] + '...'
                        else:
                            variables[k] = v
                    except:
                        variables[k] = '<无法序列化>'

            # 获取最后一个表达式的结果
            result = None
            if safe_locals:
                last_var = list(safe_locals.keys())[-1]
                if not last_var.startswith('_'):
                    result = safe_locals[last_var]

            # 构建输出
            output = {
                'result': result,
                'stdout': stdout_output,
                'stderr': stderr_output,
                'variables': variables,
                'sandbox_info': {
                    'max_code_length': MAX_CODE_LENGTH,
                    'max_execution_time': MAX_EXECUTION_TIME,
                    'max_recursion_depth': MAX_RECURSION_DEPTH,
                    'max_iterations': MAX_ITERATIONS,
                    'max_output_size': MAX_OUTPUT_SIZE,
                    'max_variables': MAX_VARIABLES,
                }
            }

        return make_success_result(
            'code_executor',
            input_data,
            output,
            timer.elapsed_ms
        )

    except SkillError as exc:
        return make_error_result('code_executor', exc, input_data, timer.elapsed_ms if 'timer' in dir() else 0.0)

    except Exception as exc:
        return make_error_result('code_executor', exc, input_data, timer.elapsed_ms if 'timer' in dir() else 0.0)


# ==================================================================================
# 模块信息
# ==================================================================================

def get_module_info() -> dict:
    """
    获取模块信息

    Returns:
        模块信息字典
    """
    return {
        'name': 'code_executor',
        'description': '安全的沙箱代码执行器',
        'version': '2.0.0',
        'sandbox_limits': {
            'max_code_length': MAX_CODE_LENGTH,
            'max_execution_time': MAX_EXECUTION_TIME,
            'max_recursion_depth': MAX_RECURSION_DEPTH,
            'max_iterations': MAX_ITERATIONS,
            'max_output_size': MAX_OUTPUT_SIZE,
            'max_variables': MAX_VARIABLES,
            'max_string_length': MAX_STRING_LENGTH,
            'max_list_length': MAX_LIST_LENGTH,
            'max_dict_size': MAX_DICT_SIZE,
        },
        'forbidden_operations': [
            'import语句',
            'exec/eval/compile函数',
            'open文件操作',
            'os/sys/subprocess模块',
            'getattr/setattr/delattr函数',
            '内省属性访问（__class__, __bases__等）',
            '__builtins__下标访问',
        ],
        'safe_modules': [
            'math', 'random', 'string', 'collections', 'itertools',
            'functools', 'operator', 'json', 're', 'datetime',
            'decimal', 'fractions', 'statistics', 'copy',
        ],
    }
