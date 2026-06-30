"""Code Executor - 安全沙箱Python代码执行器，多重安全限制"""

from __future__ import annotations

import ast, io, sys, traceback
from contextlib import redirect_stdout, redirect_stderr
from time import perf_counter

from skills.exceptions import (
    SkillError, ExecutionError, InputValueError, ParseError, SecurityError,
)
from skills.error_utils import (
    make_error_result, make_success_result, measure_latency,
    validate_max_length, validate_not_empty_string,
)

# ---- 沙箱限制 ----
MAX_CODE, MAX_TIME, MAX_RECUR, MAX_OUT, MAX_VARS = 10000, 5, 100, 100000, 100
MAX_ITERS, MAX_STR, MAX_LIST = 100000, 10000, 10000

FORBIDDEN_ASTS = {ast.Global, ast.Nonlocal}
[FORBIDDEN_ASTS.add(getattr(ast, a)) for a in ('Exec', 'Print') if hasattr(ast, a)]

FORBIDDEN_FUNCS = {'exec', 'eval', 'compile', '__import__', 'open', 'input', 'breakpoint',
    'exit', 'quit', 'help', 'getattr', 'setattr', 'delattr', 'globals', 'locals', 'vars',
    'dir', 'type', 'id', 'hash', 'callable', 'super', 'memoryview', 'bytearray', 'bytes'}

FORBIDDEN_MODS = {'os', 'sys', 'subprocess', 'shutil', 'socket', 'http', 'urllib', 'requests',
    'ctypes', 'importlib', 'inspect', 'code', 'pickle', 'shelve', 'marshal', 'sqlite3',
    'threading', 'multiprocessing', 'concurrent', 'asyncio', 'signal', 'ssl', 'hashlib'}

FORBIDDEN_ATTRS = {'__class__', '__bases__', '__subclasses__', '__mro__', '__globals__',
    '__locals__', '__builtins__', '__import__', '__loader__', '__spec__', '__file__',
    '__dict__', '__reduce__', '__reduce_ex__', '__new__', '__init__', '__del__'}

SAFE_MODS = {'math', 'random', 'string', 'collections', 'itertools', 'functools',
    'operator', 'json', 're', 'datetime', 'decimal', 'fractions', 'statistics', 'copy'}


# ---- AST 安全验证 ----

def _validate_ast(code: str) -> None:
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise ParseError(code="CODE-EXEC-001", message=f"语法错误：{exc.msg}",
                         details={"line": exc.lineno, "offset": exc.offset}) from exc

    for node in ast.walk(tree):
        if type(node) in FORBIDDEN_ASTS:
            raise SecurityError(code="CODE-SEC-001", message=f"禁止的代码结构：{type(node).__name__}")

        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mn = node.names[0].name if isinstance(node, ast.Import) else (node.module or "")
            if mn.split('.')[0] not in SAFE_MODS:
                raise SecurityError(code="CODE-SEC-001", message=f"禁止import：{mn}",
                                    details={"module": mn})

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_FUNCS:
            raise SecurityError(code="CODE-SEC-002", message=f"禁止函数：{node.func.id}")

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
           isinstance(node.func.value, ast.Name) and node.func.value.id in FORBIDDEN_MODS:
            raise SecurityError(code="CODE-SEC-003", message=f"禁止模块调用：{node.func.value.id}.{node.func.attr}")

        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRS:
            raise SecurityError(code="CODE-SEC-005", message=f"禁止属性访问：{node.attr}")

        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and \
           node.value.id in ('__builtins__', '__globals__', '__locals__'):
            raise SecurityError(code="CODE-SEC-006", message=f"禁止下标访问：{node.value.id}[...]")


# ---- 安全执行环境 ----

def _make_globals() -> dict:
    import math, random, string, collections, itertools, functools, operator
    import json as _json, re as _re, datetime, decimal, fractions, statistics, copy

    builtins = {
        'abs': abs, 'all': all, 'any': any, 'bool': bool, 'chr': chr, 'dict': dict,
        'divmod': divmod, 'enumerate': enumerate, 'filter': filter, 'float': float,
        'format': format, 'frozenset': frozenset, 'hex': hex, 'int': int, 'iter': iter,
        'len': len, 'list': list, 'map': map, 'max': max, 'min': min, 'next': next,
        'oct': oct, 'ord': ord, 'pow': pow, 'print': print, 'range': range, 'repr': repr,
        'reversed': reversed, 'round': round, 'set': set, 'slice': slice, 'sorted': sorted,
        'str': str, 'sum': sum, 'tuple': tuple, 'zip': zip, 'bin': bin, 'complex': complex,
        'object': object, 'property': property, 'staticmethod': staticmethod,
        'classmethod': classmethod, 'hasattr': hasattr, 'isinstance': isinstance,
        'issubclass': issubclass, 'callable': callable, 'hash': hash,
    }

    safe_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__
    builtins['__import__'] = lambda name, *a, **kw: (
        safe_import(name, *a, **kw) if name.split('.')[0] in SAFE_MODS
        else (_ for _ in ()).throw(ImportError(f"禁止导入：{name}")))

    # 受限 range
    _range = range
    builtins['range'] = lambda *a: (
        (_ for _ in ()).throw(ExecutionError(code="CODE-SEC-004", message=f"range范围超过{MAX_ITERS}"))
        if (len(a) == 1 and a[0] > MAX_ITERS) or (len(a) == 2 and a[1] - a[0] > MAX_ITERS)
        else _range(*a))

    return {'__builtins__': builtins, 'math': math, 'random': random, 'string': string,
            'collections': collections, 'itertools': itertools, 'functools': functools,
            'operator': operator, 'json': _json, 're': _re, 'datetime': datetime,
            'decimal': decimal, 'fractions': fractions, 'statistics': statistics, 'copy': copy,
            'pi': math.pi, 'e': math.e, 'tau': math.tau, 'inf': math.inf, 'nan': math.nan}


# ---- 主函数 ----

def code_executor(code: str, timeout: int = MAX_TIME, capture_print: bool = True) -> dict:
    input_data = {'code': code[:100] + '...' if isinstance(code, str) and len(code) > 100 else code}

    try:
        with measure_latency() as timer:
            validate_not_empty_string(code, 'code', 'code_executor', 'CODE-VAL-001')
            validate_max_length(code, MAX_CODE, 'code', 'code_executor', 'CODE-VAL-002')
            if not (1 <= timeout <= 30):
                raise InputValueError(code='CODE-VAL-003', message='timeout 需在 1-30 之间')

            _validate_ast(code)

            stdout_cap, stderr_cap = io.StringIO(), io.StringIO()
            old_limit, safe_locals = sys.getrecursionlimit(), {}
            sys.setrecursionlimit(MAX_RECUR)

            try:
                with redirect_stdout(stdout_cap), redirect_stderr(stderr_cap):
                    exec(compile(code, '<input>', 'exec'), _make_globals(), safe_locals)
            except RecursionError:
                raise ExecutionError(code="CODE-SEC-009", message=f"递归超限 >{MAX_RECUR}")
            except ExecutionError:
                raise
            except Exception as exc:
                tb = traceback.format_exc()
                raise ExecutionError(code="CODE-EXEC-003", message=f"执行错误：{exc}",
                                     details={"error_type": type(exc).__name__, "traceback": tb}) from exc
            finally:
                sys.setrecursionlimit(old_limit)

            out = stdout_cap.getvalue()[:MAX_OUT]
            err = stderr_cap.getvalue()[:MAX_OUT]
            if len(stdout_cap.getvalue()) > MAX_OUT:
                out += f"\n... 截断 >{MAX_OUT}"
            if len(stderr_cap.getvalue()) > MAX_OUT:
                err += f"\n... 截断 >{MAX_OUT}"

            vars_ = {}
            for k, v in safe_locals.items():
                if not k.startswith('_') and len(vars_) < MAX_VARS:
                    try:
                        rp = repr(v)
                        vars_[k] = v if len(rp) <= MAX_STR else rp[:MAX_STR] + '...'
                    except:
                        vars_[k] = '<unrepresentable>'

            last = next((safe_locals[k] for k in reversed(list(safe_locals.keys()))
                        if not k.startswith('_')), None)

            output = {'result': last, 'stdout': out, 'stderr': err, 'variables': vars_,
                      'sandbox': {'max_code': MAX_CODE, 'max_time': MAX_TIME, 'max_recursion': MAX_RECUR,
                                  'max_iters': MAX_ITERS, 'max_output': MAX_OUT, 'max_vars': MAX_VARS}}

        return make_success_result('code_executor', input_data, output, timer.elapsed_ms)

    except SkillError as exc:
        return make_error_result('code_executor', exc, input_data)
    except Exception as exc:
        return make_error_result('code_executor', exc, input_data)
