"""
Skills 统一交互式测试文件

运行方式：
    python -m skills.test_all
"""

from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from skills.calculator import calculator
from skills.file_reader import file_reader
from skills.local_file_search import local_file_search
from skills.table_analyzer import table_analyzer
from skills.format_converter import format_converter
from skills.code_executor import code_executor
from skills.text_summarizer import text_summarizer
from skills.compound import search_read_summarize, read_analyze_format, calculate_format, read_summarize_format
from skills import resolve_data_path


# ==================================================================================
# 测试函数
# ==================================================================================

def test_calculator():
    """测试 Calculator Skill"""
    print()
    print("-" * 50)
    print("Calculator Skill 测试")
    print("-" * 50)
    print()
    print("支持: +, -, *, /, //, %, **")
    print("函数: abs, round, min, max, sum, int, float")
    print("常量: pi, e, tau, inf, nan")
    print()
    print("示例: 23 * 17 + 9, abs(-5), pi * 2")
    print()

    while True:
        expr = input("[Calculator] 请输入表达式 (q=退出): ").strip()
        if expr.lower() in ('q', 'quit', 'exit'):
            break
        if not expr:
            continue

        result = calculator(expr)
        if result["status"] == "success":
            print(f"  ✓ 结果: {result['output']['result']}  ({result['latency_ms']:.3f}ms)")
        else:
            print(f"  ✗ 错误 [{result['error']['code']}]: {result['error']['message']}")
            if result['error'].get('suggestion'):
                print(f"    建议: {result['error']['suggestion']}")
        print()


def test_file_reader():
    """测试 File Reader Skill"""
    print()
    print("-" * 50)
    print("File Reader Skill 测试")
    print("-" * 50)
    print()
    print("支持: .txt, .md, .py, .json, .yaml, .yml, .csv")
    print()
    print("可用文件: docs/agent_intro.txt, docs/search_skill_demo.md, tables/results.csv")
    print()
    print("格式: <路径> [最大字符数] [true=包含元数据]")
    print()

    while True:
        user_input = input("[FileReader] 请输入路径 (q=退出): ").strip()
        if user_input.lower() in ('q', 'quit', 'exit'):
            break
        if not user_input:
            continue

        parts = user_input.split()
        path = parts[0]
        max_chars = int(parts[1]) if len(parts) > 1 else 2000
        metadata = parts[2].lower() in ('true', '1', 'yes') if len(parts) > 2 else False

        result = file_reader(path, max_chars, metadata)
        if result["status"] == "success":
            out = result["output"]
            print(f"  ✓ 文件: {out['source']}")
            print(f"    字符: {out['num_chars']}, 截断: {out['truncated']}, 耗时: {result['latency_ms']:.3f}ms")
            content = out['content']
            print(f"    内容: {content[:200]}{'...' if len(content) > 200 else ''}")
            if metadata and 'metadata' in out:
                m = out['metadata']
                print(f"    元数据: {m['size_human']}, 编码: {m['encoding']}")
        else:
            print(f"  ✗ 错误 [{result['error']['code']}]: {result['error']['message']}")
        print()


def test_local_file_search():
    """测试 Local File Search Skill"""
    print()
    print("-" * 50)
    print("Local File Search Skill 测试")
    print("-" * 50)
    print()
    print("选项: --regex, --top N, --dir DIR, --exclude PAT, --matches")
    print()
    print("示例: Agent, Agent 工具 --top 3, test --regex")
    print()

    while True:
        user_input = input("[Search] 请输入查询 (q=退出): ").strip()
        if user_input.lower() in ('q', 'quit', 'exit'):
            break
        if not user_input:
            continue

        parts = user_input.split()
        query = parts[0]
        use_regex = False
        top_k = 5
        root_dir = "docs"
        exclude = None
        show_matches = False

        i = 1
        while i < len(parts):
            if parts[i] == '--regex':
                use_regex = True
                i += 1
            elif parts[i] == '--top' and i + 1 < len(parts):
                top_k = int(parts[i + 1])
                i += 2
            elif parts[i] == '--dir' and i + 1 < len(parts):
                root_dir = parts[i + 1]
                i += 2
            elif parts[i] == '--exclude' and i + 1 < len(parts):
                exclude = [parts[i + 1]]
                i += 2
            elif parts[i] == '--matches':
                show_matches = True
                i += 1
            else:
                query += " " + parts[i]
                i += 1

        result = local_file_search(
            query=query,
            root_dir=root_dir,
            top_k=top_k,
            use_regex=use_regex,
            exclude_patterns=exclude,
            include_matches=show_matches,
        )

        if result["status"] == "success":
            results = result["output"]["results"]
            print(f"  ✓ 找到 {len(results)} 个结果 ({result['latency_ms']:.3f}ms)")
            for i, r in enumerate(results, 1):
                print(f"    [{i}] {r['path']} (分数: {r['score']})")
                print(f"        {r['snippet'][:100]}...")
                if show_matches and 'matches' in r:
                    print(f"        匹配数: {len(r['matches'])}")
        else:
            print(f"  ✗ 错误 [{result['error']['code']}]: {result['error']['message']}")
        print()


def test_table_analyzer():
    """测试 Table Analyzer Skill"""
    print()
    print("-" * 50)
    print("Table Analyzer Skill 测试")
    print("-" * 50)
    print()
    print("支持: .csv, .tsv, .jsonl")
    print()
    print("可用文件: tables/results.csv")
    print()
    print("选项: --preview N, --no-describe, --quality, --outliers")
    print()

    while True:
        user_input = input("[Table] 请输入路径 (q=退出): ").strip()
        if user_input.lower() in ('q', 'quit', 'exit'):
            break
        if not user_input:
            continue

        parts = user_input.split()
        path = parts[0]
        preview = 5
        describe = True
        quality = False
        outliers = False

        i = 1
        while i < len(parts):
            if parts[i] == '--preview' and i + 1 < len(parts):
                preview = int(parts[i + 1])
                i += 2
            elif parts[i] == '--no-describe':
                describe = False
                i += 1
            elif parts[i] == '--quality':
                quality = True
                i += 1
            elif parts[i] == '--outliers':
                outliers = True
                i += 1
            else:
                i += 1

        result = table_analyzer(
            path=path,
            max_rows_preview=preview,
            describe=describe,
            check_quality=quality,
            detect_outliers=outliers,
        )

        if result["status"] == "success":
            out = result["output"]
            print(f"  ✓ 文件: {out['path']}")
            print(f"    行数: {out['num_rows']}, 列数: {out['num_columns']}")
            print(f"    列名: {', '.join(out['columns'])}")
            print(f"    耗时: {result['latency_ms']:.3f}ms")

            print(f"    预览:")
            for i, row in enumerate(out['preview'], 1):
                print(f"      [{i}] {row}")

            if describe and out['describe']:
                print(f"    统计:")
                for col, s in out['describe'].items():
                    print(f"      {col}: min={s['min']}, max={s['max']}, mean={s['mean']:.2f}")

            if quality and 'quality' in out:
                q = out['quality']
                print(f"    质量: 空行={q['empty_rows']}, 类型={q['data_types']}")

            if outliers and 'outliers' in out:
                o = out['outliers']
                if o:
                    for col, info in o.items():
                        print(f"    异常值 [{col}]: {info['count']}个")
                else:
                    print(f"    无异常值")
        else:
            print(f"  ✗ 错误 [{result['error']['code']}]: {result['error']['message']}")
        print()


def test_format_converter():
    """测试 Format Converter Skill"""
    print()
    print("-" * 50)
    print("Format Converter Skill 测试")
    print("-" * 50)
    print()
    print("支持格式: markdown, json, csv, yaml, html")
    print()
    print("格式: <目标格式> <内容>")
    print("      <目标格式> --reverse <JSON内容>")
    print()
    print("示例: markdown name: Agent Demo")
    print("      json name: Agent Demo")
    print()

    while True:
        user_input = input("[Converter] 请输入 (q=退出): ").strip()
        if user_input.lower() in ('q', 'quit', 'exit'):
            break
        if not user_input:
            continue

        parts = user_input.split(maxsplit=2)
        if len(parts) < 2:
            print("  ⚠ 格式: <目标格式> <内容>")
            continue

        target_format = parts[0]
        reverse = False

        if parts[1] == '--reverse':
            reverse = True
            text = parts[2] if len(parts) > 2 else ""
        else:
            text = parts[1] + (" " + parts[2] if len(parts) > 2 else "")

        if not text:
            print("  ⚠ 内容不能为空")
            continue

        result = format_converter(
            text=text,
            target_format=target_format,
            reverse=reverse,
        )

        if result["status"] == "success":
            out = result["output"]
            print(f"  ✓ 转换成功 ({result['latency_ms']:.3f}ms)")
            print(f"    文件: {out['generated_file_path']}")
            print(f"    结果:")
            for line in out['formatted_text'].split('\n'):
                print(f"      {line}")
        else:
            print(f"  ✗ 错误 [{result['error']['code']}]: {result['error']['message']}")
        print()


def test_path_resolver():
    """测试路径解析"""
    print()
    print("-" * 50)
    print("Path Resolver 测试")
    print("-" * 50)
    print()
    print("示例: docs/agent_intro.txt, ../../etc/passwd")
    print()

    while True:
        user_input = input("[Path] 请输入路径 (q=退出): ").strip()
        if user_input.lower() in ('q', 'quit', 'exit'):
            break
        if not user_input:
            continue

        parts = user_input.split()
        path = parts[0]
        data_root = parts[1] if len(parts) > 1 else None

        try:
            candidate, root = resolve_data_path(path, data_root)
            print(f"  ✓ 解析成功")
            print(f"    根目录: {root}")
            print(f"    结果: {candidate}")
            print(f"    存在: {candidate.exists()}")
        except Exception as e:
            print(f"  ✗ 错误: {e}")
        print()


def test_code_executor():
    """测试 Code Executor Skill"""
    print()
    print("-" * 50)
    print("Code Executor Skill 测试")
    print("-" * 50)
    print()
    print("安全限制:")
    print("  - 最大代码长度: 10000 字符")
    print("  - 最大执行时间: 5 秒")
    print("  - 禁止import语句")
    print("  - 禁止文件系统操作")
    print()
    print("示例:")
    print("  print(2 + 3)")
    print("  x = 10")
    print("  y = x ** 2")
    print("  print(y)")
    print("  [i**2 for i in range(10)]")
    print()
    print("多行代码: 输入 'END' 结束")
    print()

    while True:
        user_input = input("[Code] 请输入代码 (q=退出, END=执行多行): ").strip()
        if user_input.lower() in ('q', 'quit', 'exit'):
            break
        if not user_input:
            continue

        # 多行代码模式
        if user_input.upper() == 'END':
            print("  ⚠ 请先输入代码，再输入END")
            continue

        # 检查是否是多行代码的开始
        code_lines = [user_input]
        if user_input.endswith(':') or user_input.startswith('def ') or user_input.startswith('class ') or user_input.startswith('for ') or user_input.startswith('while ') or user_input.startswith('if '):
            print("  输入代码，完成后输入 'END' 执行:")
            while True:
                line = input("  ... ")
                if line.strip().upper() == 'END':
                    break
                code_lines.append(line)

        code = "\n".join(code_lines)

        result = code_executor(code)

        if result["status"] == "success":
            out = result["output"]
            print(f"  ✓ 执行成功 ({result['latency_ms']:.3f}ms)")

            if out['stdout']:
                print(f"    输出:")
                for line in out['stdout'].rstrip().split('\n'):
                    print(f"      {line}")

            if out['stderr']:
                print(f"    错误:")
                for line in out['stderr'].rstrip().split('\n'):
                    print(f"      {line}")

            if out['result'] is not None:
                print(f"    结果: {out['result']}")

            if out['variables']:
                print(f"    变量:")
                for k, v in out['variables'].items():
                    print(f"      {k} = {repr(v)}")
        else:
            print(f"  ✗ 错误 [{result['error']['code']}]: {result['error']['message']}")
            if result['error'].get('details', {}).get('traceback'):
                print(f"    追踪:")
                for line in result['error']['details']['traceback'].split('\n')[-3:]:
                    print(f"      {line}")
        print()


def test_text_summarizer():
    """测试 Text Summarizer Skill"""
    print()
    print("-" * 50)
    print("Text Summarizer Skill 测试")
    print("-" * 50)
    print()
    print("功能: 提取关键句子和关键词，生成文本摘要")
    print()
    print("参数:")
    print("  max_sentences  最大摘要句子数 (默认3)")
    print("  max_keywords   最大关键词数 (默认10)")
    print()
    print("示例:")
    print("  Agent系统由模型、工具、记忆和执行循环组成。工具调用让模型能够读取本地文件。Memory为Agent提供全局知识。")
    print()
    print("多行文本: 输入 'END' 结束")
    print()

    while True:
        user_input = input("[Summarizer] 请输入文本 (q=退出, END=执行多行): ").strip()
        if user_input.lower() in ('q', 'quit', 'exit'):
            break
        if not user_input:
            continue

        # 多行文本模式
        text_lines = [user_input]
        if user_input.upper() == 'END':
            print("  ⚠ 请先输入文本，再输入END")
            continue

        # 检查是否需要多行输入
        print("  输入更多文本，完成后输入 'END' 执行 (直接END只处理第一行):")
        while True:
            line = input("  ... ")
            if line.strip().upper() == 'END':
                break
            text_lines.append(line)

        text = "\n".join(text_lines)

        # 解析参数
        max_sentences = 3
        max_keywords = 10

        # 检查是否有参数
        parts = text.rsplit('\n', 1)
        if len(parts) > 1:
            last_line = parts[-1].strip()
            if last_line.startswith('--sentences'):
                try:
                    max_sentences = int(last_line.split('=')[1])
                    text = parts[0]
                except:
                    pass
            elif last_line.startswith('--keywords'):
                try:
                    max_keywords = int(last_line.split('=')[1])
                    text = parts[0]
                except:
                    pass

        result = text_summarizer(
            text=text,
            max_sentences=max_sentences,
            max_keywords=max_keywords,
        )

        if result["status"] == "success":
            out = result["output"]
            print(f"  ✓ 摘要生成成功 ({result['latency_ms']:.3f}ms)")
            print()
            print(f"    语言: {out['stats']['language']}")
            print(f"    原文: {out['stats']['total_chars']}字符, {out['stats']['total_sentences']}句, {out['stats']['total_words']}词")
            print(f"    压缩率: {out['stats']['compression_ratio']:.1%}")
            print()
            print(f"    摘要:")
            print(f"      {out['summary']}")
            print()
            print(f"    关键句 ({len(out['key_sentences'])}句):")
            for i, s in enumerate(out['key_sentences'], 1):
                print(f"      [{i}] {s}")
            print()
            if out['keywords']:
                print(f"    关键词 ({len(out['keywords'])}个):")
                for kw in out['keywords']:
                    print(f"      {kw['word']} (出现{kw['count']}次, 频率{kw['frequency']:.1%})")
        else:
            print(f"  ✗ 错误 [{result['error']['code']}]: {result['error']['message']}")
        print()


def test_compound_search_read_summarize():
    print()
    print("-" * 50)
    print("[Compound] Search → Read → Summarize")
    print("-" * 50)
    while True:
        q = input("[Search+Read+Summarize] 请输入查询 (q=退出): ").strip()
        if q.lower() in ('q', 'quit', 'exit'): break
        if not q: continue
        r = search_read_summarize(q)
        if r['status'] == 'success':
            o = r['output']
            print(f"  ✓ 源文件: {o['source']}")
            print(f"  摘要: {o['summary'][:120]}...")
            print(f"  关键词: {[k['word'] for k in o['keywords']]}")
        else:
            print(f"  ✗ {r['error']['message']}")
        print()

def test_compound_read_analyze_format():
    print()
    print("-" * 50)
    print("[Compound] Read → Analyze → Format")
    print("-" * 50)
    while True:
        p = input("[Read+Analyze+Format] 请输入路径 (q=退出): ").strip()
        if p.lower() in ('q', 'quit', 'exit'): break
        if not p: continue
        parts = p.split(); path = parts[0]; fmt = parts[1] if len(parts)>1 else 'json'
        r = read_analyze_format(path, fmt)
        if r['status'] == 'success':
            o = r['output']
            print(f"  ✓ 文件: {o['source']}, 行数: {o['data']['rows']}")
            print(f"  报告: {o['report'][:200]}")
        else:
            print(f"  ✗ {r['error']['message']}")
        print()

def test_compound_calculate_format():
    print()
    print("-" * 50)
    print("[Compound] Calculate → Format")
    print("-" * 50)
    while True:
        e = input("[Calculate+Format] 请输入表达式 (q=退出): ").strip()
        if e.lower() in ('q', 'quit', 'exit'): break
        if not e: continue
        r = calculate_format(e, 'markdown')
        if r['status'] == 'success':
            o = r['output']
            print(f"  ✓ 结果: {o['result']}")
            print(f"  {o['formatted']}")
        else:
            print(f"  ✗ {r['error']['message']}")
        print()

def test_compound_read_summarize_format():
    print()
    print("-" * 50)
    print("[Compound] Read → Summarize → Format")
    print("-" * 50)
    while True:
        p = input("[Read+Summarize+Format] 请输入路径 (q=退出): ").strip()
        if p.lower() in ('q', 'quit', 'exit'): break
        if not p: continue
        r = read_summarize_format(p, 'markdown')
        if r['status'] == 'success':
            o = r['output']
            print(f"  ✓ 文件: {o['source']}")
            print(f"  摘要: {o['summary'][:100]}")
            print(f"  关键词: {o['keywords']}")
        else:
            print(f"  ✗ {r['error']['message']}")
        print()


# ==================================================================================
# 主菜单
# ==================================================================================

def main():
    """主菜单"""
    print("=" * 60)
    print("Skills 统一交互式测试")
    print("=" * 60)
    print()

    menu = {
        "1": ("Calculator (计算器)", test_calculator),
        "2": ("File Reader (文件读取)", test_file_reader),
        "3": ("Local File Search (文件搜索)", test_local_file_search),
        "4": ("Table Analyzer (表格分析)", test_table_analyzer),
        "5": ("Format Converter (格式转换)", test_format_converter),
        "6": ("Code Executor (代码执行)", test_code_executor),
        "7": ("Text Summarizer (文本摘要)", test_text_summarizer),
        "8": ("[Compound] Search+Read+Summarize", test_compound_search_read_summarize),
        "9": ("[Compound] Read+Analyze+Format", test_compound_read_analyze_format),
        "a": ("[Compound] Calculate+Format", test_compound_calculate_format),
        "b": ("[Compound] Read+Summarize+Format", test_compound_read_summarize_format),
        "0": ("Path Resolver (路径解析)", test_path_resolver),
    }

    while True:
        print("请选择要测试的 Skill:")
        print()
        for key, (name, _) in menu.items():
            print(f"  [{key}] {name}")
        print()
        print("  [0] 退出")
        print()

        choice = input("请输入选项: ").strip()

        if choice in ('0', 'q', 'quit', 'exit'):
            print("再见！")
            break

        if choice in menu:
            _, func = menu[choice]
            func()
        else:
            print("⚠ 无效选项，请重新输入")

        print()


if __name__ == "__main__":
    main()
