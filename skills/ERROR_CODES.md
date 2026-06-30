# Skills 错误码参考手册

## 📋 错误码格式

```
[Skill前缀]-[错误分类]-[错误编号]

示例：CALC-VAL-001
├── CALC: Skill前缀（calculator）
├── VAL: 错误分类（验证错误）
└── 001: 错误编号
```

---

## 🏷️ Skill前缀

| Skill | 前缀 | 说明 |
|-------|------|------|
| calculator | `CALC` | 计算器 |
| file_reader | `FREAD` | 文件读取 |
| local_file_search | `FSEARCH` | 文件搜索 |
| table_analyzer | `TANAL` | 表格分析 |
| format_converter | `FCONV` | 格式转换 |
| code_executor | `CODE` | 代码执行 |
| 路径相关 | `PATH` | 通用路径错误 |

---

## 📂 错误分类

| 分类 | 代码 | 说明 |
|------|------|------|
| **VAL** | Validation | 验证错误（输入参数问题） |
| **EXEC** | Execution | 执行错误（运行时问题） |
| **SEC** | Security | 安全错误（安全相关问题） |

---

## 📊 完整错误码表

### Calculator (CALC)

| 错误码 | 类型 | 说明 | 示例 |
|--------|------|------|------|
| `CALC-VAL-001` | InputTypeError | 表达式必须是字符串 | `calculator(123)` |
| `CALC-VAL-002` | InputValueError | 表达式不能为空 | `calculator("")` |
| `CALC-VAL-003` | InputValueError | 表达式过长（超过200字符） | `calculator("a"*201)` |
| `CALC-EXEC-001` | ParseError | 表达式语法错误 | `calculator("2+")` |
| `CALC-EXEC-002` | UnsafeExpressionError | 不支持的表达式元素 | `calculator("x+1")` |
| `CALC-EXEC-003` | CalculationError | 指数过大（超过12） | `calculator("2**100")` |
| `CALC-EXEC-004` | ResultOverflowError | 结果溢出 | `calculator("1e200**2")` |
| `CALC-SEC-001` | UnsafeExpressionError | 不安全的函数调用 | `calculator("__import__('os')")` |
| `CALC-SEC-002` | UnsafeExpressionError | 不安全的变量引用 | `calculator("os.system")` |

---

### File Reader (FREAD)

| 错误码 | 类型 | 说明 | 示例 |
|--------|------|------|------|
| `FREAD-VAL-002` | InputValueError | max_chars必须是正整数 | `file_reader("f.txt", max_chars=-1)` |
| `FREAD-VAL-003` | InvalidFormatError | 不支持的文件类型 | `file_reader("f.xyz")` |
| `FREAD-EXEC-001` | FileNotFoundError | 文件不存在 | `file_reader("nonexistent.txt")` |
| `FREAD-EXEC-002` | EncodingError | 文件编码错误 | `file_reader("binary.bin")` |

---

### Local File Search (FSEARCH)

| 错误码 | 类型 | 说明 | 示例 |
|--------|------|------|------|
| `FSEARCH-VAL-002` | InputValueError | 查询不能为空 | `local_file_search("")` |
| `FSEARCH-VAL-003` | InputValueError | top_k必须是正整数 | `local_file_search("q", top_k=0)` |
| `FSEARCH-VAL-004` | InvalidFormatError | 不支持的文件类型 | `local_file_search("q", file_types=["pdf"])` |
| `FSEARCH-EXEC-001` | FileNotFoundError | 搜索目录不存在 | `local_file_search("q", root_dir="nonexistent")` |
| `FSEARCH-EXEC-002` | ParseError | 正则表达式无效 | `local_file_search("[invalid", use_regex=True)` |

---

### Table Analyzer (TANAL)

| 错误码 | 类型 | 说明 | 示例 |
|--------|------|------|------|
| `TANAL-VAL-002` | InputValueError | max_rows_preview必须是非负整数 | `table_analyzer("f.csv", max_rows_preview=-1)` |
| `TANAL-VAL-003` | InvalidFormatError | 不支持的文件类型 | `table_analyzer("f.txt")` |
| `TANAL-EXEC-001` | FileNotFoundError | 文件不存在 | `table_analyzer("nonexistent.csv")` |
| `TANAL-EXEC-002` | ParseError | JSON解析失败 | `table_analyzer("invalid.jsonl")` |
| `TANAL-EXEC-003` | ParseError | 缺少表头行 | `table_analyzer("no_header.csv")` |

---

### Format Converter (FCONV)

| 错误码 | 类型 | 说明 | 示例 |
|--------|------|------|------|
| `FCONV-VAL-002` | InputValueError | 文本不能为空 | `format_converter("", "json")` |
| `FCONV-VAL-003` | InvalidFormatError | 不支持的目标格式 | `format_converter("t", "xml")` |
| `FCONV-EXEC-001` | ParseError | 解析失败 | `format_converter("{invalid}", "json")` |
| `FCONV-EXEC-002` | ParseError | key-value格式错误 | `format_converter("no colon", "json")` |
| `FCONV-EXEC-003` | ParseError | 重复的key | `format_converter("a:1\na:2", "json")` |

---

### Code Executor (CODE)

| 错误码 | 类型 | 说明 | 示例 |
|--------|------|------|------|
| `CODE-VAL-001` | InputValueError | 代码不能为空 | `code_executor("")` |
| `CODE-VAL-002` | InputValueError | 代码过长（超过10000字符） | `code_executor("x=1"*5000)` |
| `CODE-VAL-003` | InputValueError | timeout必须是1-30之间的整数 | `code_executor("x=1", timeout=100)` |
| `CODE-EXEC-001` | ParseError | 代码语法错误 | `code_executor("def foo(:")` |
| `CODE-EXEC-002` | TimeoutError | 代码执行超时 | `code_executor("while True: pass")` |
| `CODE-EXEC-003` | ExecutionError | 代码执行错误 | `code_executor("1/0")` |
| `CODE-SEC-001` | SecurityError | 禁止的import | `code_executor("import os")` |
| `CODE-SEC-002` | SecurityError | 禁止的函数调用 | `code_executor("exec('1+1')")` |
| `CODE-SEC-003` | SecurityError | 禁止的模块调用 | `code_executor("os.system('ls')")` |
| `CODE-SEC-004` | ExecutionError | 循环迭代超过限制 | `code_executor("for i in range(200000): pass")` |
| `CODE-SEC-005` | SecurityError | 禁止的属性访问 | `code_executor("x = ''.__class__")` |
| `CODE-SEC-006` | SecurityError | 禁止的下标访问 | `code_executor("x = __builtins__['__import__']")` |
| `CODE-SEC-007` | ExecutionError | 字符串长度超限 | `code_executor("x = 'a' * 20000")` |
| `CODE-SEC-008` | ExecutionError | 列表长度超限 | `code_executor("x = list(range(20000))")` |
| `CODE-SEC-009` | ExecutionError | 递归深度超限 | `code_executor("def f(n): return f(n-1)")` |

---

### 路径相关 (PATH)

| 错误码 | 类型 | 说明 | 示例 |
|--------|------|------|------|
| `PATH-SEC-001` | PathEscapeError | 路径逃逸 | `resolve_data_path("../../etc/passwd")` |

---

## 🔧 异常类层次结构

```
SkillError (基类)
├── ValidationError (验证错误)
│   ├── InputTypeError        # 输入类型错误
│   ├── InputValueError       # 输入值错误
│   ├── MissingParameterError # 缺少必填参数
│   └── InvalidFormatError    # 格式无效
├── ExecutionError (执行错误)
│   ├── FileNotFoundError     # 文件不存在
│   ├── PermissionError       # 权限不足
│   ├── ParseError            # 解析失败
│   ├── CalculationError      # 计算错误
│   └── EncodingError         # 编码错误
├── ResourceError (资源错误)
│   ├── FileSizeError         # 文件大小超限
│   ├── MemoryLimitError      # 内存超限
│   ├── TimeoutError          # 执行超时
│   └── ResultOverflowError   # 结果溢出
└── SecurityError (安全错误)
    ├── PathEscapeError       # 路径逃逸
    ├── UnsafeExpressionError # 不安全表达式
    ├── RestrictedOperationError # 受限操作
    └── UnsafeFunctionError   # 不安全函数调用
```