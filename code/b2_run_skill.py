"""B2 Skill Runner — 独立运行和交互测试所有Skill"""

from __future__ import annotations

import argparse, importlib, inspect, sys
from pathlib import Path
from time import perf_counter

from common.io_utils import append_jsonl, read_json, write_json
from common.logging_utils import now_iso
from common.path_utils import DEFAULT_DATA_ROOT, bootstrap_project_root, resolve_cli_path
from common.schemas import make_skill_result

bootstrap_project_root()

SKILL_MODULES = {
    "calculator":               "skills.calculator",
    "file_reader":              "skills.file_reader",
    "local_file_search":        "skills.local_file_search",
    "table_analyzer":           "skills.table_analyzer",
    "format_converter":         "skills.format_converter",
    "code_executor":            "skills.code_executor",
    "text_summarizer":          "skills.text_summarizer",
    "search_read_summarize":    "skills.compound",
    "read_analyze_format":      "skills.compound",
    "calculate_format":         "skills.compound",
    "read_summarize_format":    "skills.compound",
}


def run_skill(skill_name: str, input_data: dict, data_root: str | None = None, output_dir: str | None = None) -> dict:
    """执行单个Skill并返回SkillResult"""
    if skill_name not in SKILL_MODULES:
        raise ValueError(f"unknown skill: {skill_name}")
    if not isinstance(input_data, dict):
        raise ValueError("skill input must be a JSON object")

    module = importlib.import_module(SKILL_MODULES[skill_name])
    function = getattr(module, skill_name)
    kwargs = dict(input_data)
    signature = inspect.signature(function)
    if "data_root" in signature.parameters:
        kwargs["data_root"] = data_root or str(DEFAULT_DATA_ROOT)
    if "output_dir" in signature.parameters:
        kwargs["output_dir"] = output_dir

    start = perf_counter()
    try:
        output = function(**kwargs)
        # 增强型skill返回结构体而非抛异常，从返回值中提取状态
        if isinstance(output, dict) and output.get("status") == "error":
            status, error, output = "error", output.get("error"), None
        else:
            status, error = "success", None
    except Exception as exc:
        output, status = None, "error"
        error = {"type": type(exc).__name__, "message": str(exc)}
    latency_ms = round((perf_counter() - start) * 1000, 3)

    return make_skill_result(skill_name, status, input_data, output, error, latency_ms)

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run or interactively test skills.")
    parser.add_argument("--skill", choices=sorted(SKILL_MODULES), default=None)
    parser.add_argument("--input", default=None)
    parser.add_argument("--outdir", default=None,
                        help="输出目录（CLI模式必填，交互模式可选）")
    parser.add_argument("--data_root", default=None)
    parser.add_argument("--interactive", "-i", action="store_true", default=False,
                        help="启动交互式测试模式")
    return parser


# ---- 结果保存 ----

def _save_result(skill_name: str, result: dict, outdir: str | None) -> Path | None:
    """保存SkillResult到JSON文件并追加JSONL日志，返回结果路径"""
    if not outdir:
        return None
    d = resolve_cli_path(outdir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{skill_name}_result.json"
    write_json(result, path)
    append_jsonl({
        "timestamp": now_iso(), "skill_name": skill_name,
        "status": result["status"], "result_path": str(path),
        "latency_ms": result["latency_ms"],
    }, d / "skill_run_log.jsonl")
    return path


# ---- CLI 模式 ----

def _cli_mode(args) -> int:
    try:
        input_path = resolve_cli_path(args.input)
        outdir = resolve_cli_path(args.outdir)
        input_data = read_json(input_path)
        data_root = str(resolve_cli_path(args.data_root)) if args.data_root else None
        outdir.mkdir(parents=True, exist_ok=True)

        result = run_skill(args.skill, input_data, data_root, str(outdir))
        path = _save_result(args.skill, result, str(outdir))
        print(path)
        return 0
    except Exception as exc:
        print(f"fatal: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


# ---- 辅助 ----

def _call(skill: str, args: dict, outdir: str | None = None) -> dict:
    """调用skill并可选保存结果到文件"""
    r = run_skill(skill, args)
    saved = _save_result(skill, r, outdir)
    if saved:
        r["_saved"] = str(saved)
    return r


# ---- 交互模式 ----

def _print_header():
    print("\n" + "=" * 60)
    print("B2 Skills — 交互式测试")
    print("=" * 60)


def _test_calculator(outdir=None):
    print("\n[Calculator] 支持 +, -, *, /, //, %, **, 函数 abs/round/min/max, 常量 pi/e")
    print("示例: 23 * 17 + 9, abs(-5), pi * 2")
    while True:
        expr = input("\n输入表达式 (q=返回): ").strip()
        if expr.lower() in ('q', 'quit', 'exit'): break
        if not expr: continue
        r = _call("calculator", {"expression": expr}, outdir)
        _print_result(r)


def _test_file_reader(outdir=None):
    print("\n[File Reader] 可用文件: docs/agent_intro.txt, test_files/test_sample.txt, tables/results.csv")
    while True:
        user = input("\n输入路径 [max_chars] (q=返回): ").strip()
        if user.lower() in ('q', 'quit', 'exit'): break
        if not user: continue
        parts = user.split()
        path, mc = parts[0], int(parts[1]) if len(parts) > 1 else 2000
        r = _call("file_reader", {"path": path, "max_chars": mc}, outdir)
        _print_result(r, file=True)


def _test_local_file_search(outdir=None):
    print("\n[Local File Search] 选项: --top N, --regex, --matches")
    while True:
        user = input("\n输入查询 (q=返回): ").strip()
        if user.lower() in ('q', 'quit', 'exit'): break
        if not user: continue
        parts = user.split()
        query, top_k, regex, matches = parts[0], 5, False, False
        for i, p in enumerate(parts[1:]):
            if p == '--top' and i + 2 < len(parts): top_k = int(parts[i + 2])
            if p == '--regex': regex = True
            if p == '--matches': matches = True
        r = _call("local_file_search", {"query": query, "top_k": top_k, "use_regex": regex, "include_matches": matches}, outdir)
        _print_result(r, search=True)


def _test_table_analyzer(outdir=None):
    print("\n[Table Analyzer] 可用文件: tables/results.csv, test_files/test_data.csv")
    while True:
        user = input("\n输入路径 (q=返回): ").strip()
        if user.lower() in ('q', 'quit', 'exit'): break
        if not user: continue
        parts = user.split()
        path, quality = parts[0], ('--quality' in parts)
        r = _call("table_analyzer", {"path": path, "describe": True, "check_quality": quality}, outdir)
        _print_result(r, table=True)


def _test_format_converter(outdir=None):
    print("\n[Format Converter] 支持: markdown, json, yaml, csv, html")
    while True:
        user = input("\n输入 格式:文本 (q=返回): ").strip()
        if user.lower() in ('q', 'quit', 'exit'): break
        if not user: continue
        parts = user.split(':', 1)
        if len(parts) < 2: print("  格式: markdown:your text"); continue
        fmt, text = parts[0].strip(), parts[1].strip()
        r = _call("format_converter", {"text": text, "target_format": fmt}, outdir)
        _print_result(r, convert=True)


def _test_code_executor(outdir=None):
    print("\n[Code Executor] 沙箱限制: 禁止import/os, 允许math/json/re/statistics")
    while True:
        code = input("\n输入代码 (q=返回): ").strip()
        if code.lower() in ('q', 'quit', 'exit'): break
        if not code: continue
        r = _call("code_executor", {"code": code}, outdir)
        _print_result(r, code=True)


def _test_text_summarizer(outdir=None):
    print("\n[Text Summarizer] 输入文本，自动提取摘要和关键词")
    while True:
        text = input("\n输入文本 (q=返回): ").strip()
        if text.lower() in ('q', 'quit', 'exit'): break
        if not text: continue
        r = _call("text_summarizer", {"text": text, "max_sentences": 2}, outdir)
        _print_result(r, summarize=True)


def _test_compound_search_read(outdir=None):
    print("\n[Compound] Search → Read → Summarize")
    while True:
        q = input("\n输入查询 (q=返回): ").strip()
        if q.lower() in ('q', 'quit', 'exit'): break
        if not q: continue
        r = _call("search_read_summarize", {"query": q}, outdir)
        _print_result(r, compound=True)


def _test_compound_analyze(outdir=None):
    print("\n[Compound] Read → Analyze → Format")
    while True:
        p = input("\n输入文件路径 (q=返回): ").strip()
        if p.lower() in ('q', 'quit', 'exit'): break
        if not p: continue
        r = _call("read_analyze_format", {"path": p, "target_format": "json"}, outdir)
        _print_result(r, compound=True)


def _test_compound_calc(outdir=None):
    print("\n[Compound] Calculate → Format")
    while True:
        e = input("\n输入表达式 (q=返回): ").strip()
        if e.lower() in ('q', 'quit', 'exit'): break
        if not e: continue
        r = _call("calculate_format", {"expression": e, "target_format": "markdown"}, outdir)
        _print_result(r, compound=True)


def _test_compound_read_summarize(outdir=None):
    print("\n[Compound] Read → Summarize → Format")
    while True:
        p = input("\n输入文件路径 (q=返回): ").strip()
        if p.lower() in ('q', 'quit', 'exit'): break
        if not p: continue
        r = _call("read_summarize_format", {"path": p, "target_format": "markdown"}, outdir)
        _print_result(r, compound=True)


# ---- 结果格式化 ----

def _print_result(r, **opts):
    if r["status"] != "success":
        err = r.get('error', {}) if isinstance(r.get('error'), dict) else {}
        print(f"  ✗ [{err.get('code', 'UNKNOWN')}] {err.get('message', str(r.get('error', '')))}")
        return

    o = r.get("output", {})
    saved = f"  → {r.get('_saved', '')}" if r.get('_saved') else ""

    if opts.get('file'):
        print(f"  ✓ {o.get('source','?')} | {o.get('num_chars',0)}字符 | {r['latency_ms']:.3f}ms")
        print(f"  {o.get('content','')[:200]}{'...' if len(o.get('content','') or '') > 200 else ''}")
    elif opts.get('search'):
        res = o.get('results', [])
        print(f"  ✓ {len(res)} 个结果 ({r['latency_ms']:.3f}ms){saved}")
        for item in res[:3]:
            print(f"    [{item['score']}] {item['path']}: {item['snippet'][:80]}...")
    elif opts.get('table'):
        print(f"  ✓ {o.get('num_rows',0)}行 × {o.get('num_columns',0)}列 | {', '.join(o.get('columns',[]))} | {r['latency_ms']:.3f}ms{saved}")
        for col, s in o.get('describe', {}).items():
            print(f"    {col}: min={s['min']}, max={s['max']}, mean={s['mean']:.2f}")
    elif opts.get('convert'):
        print(f"  ✓ {r['latency_ms']:.3f}ms{saved}")
        for l in o.get('formatted_text', '').split('\n')[:10]:
            print(f"    {l}")
    elif opts.get('code'):
        if o.get('stdout'): print(f"  ✓ {o['stdout'].rstrip()}")
        if o.get('result') is not None: print(f"  → result: {o['result']}")
        if o.get('variables'): print(f"  → vars: {list(o['variables'].keys())}")
    elif opts.get('summarize'):
        s = o.get('stats', {})
        print(f"  ✓ 语言:{s.get('language','?')} | {s.get('total_sentences',0)}句 | {s.get('total_words',0)}词{saved}")
        print(f"  摘要: {o.get('summary','')[:120]}")
        print(f"  关键词: {[k['word'] for k in o.get('keywords',[])]}")
    elif opts.get('compound'):
        print(f"  ✓ {r['latency_ms']:.3f}ms{saved}")
        for k, v in o.items():
            if k != 'stats' and isinstance(v, (str, int, float, list)):
                val = str(v)[:120] if isinstance(v, str) else v
                print(f"  {k}: {val}")
    else:
        print(f"  → {o.get('result', o)} ({r['latency_ms']:.3f}ms){saved}")


MENU = [
    ("1",  "Calculator (计算器)",             _test_calculator),
    ("2",  "File Reader (文件读取)",           _test_file_reader),
    ("3",  "Local File Search (文件搜索)",     _test_local_file_search),
    ("4",  "Table Analyzer (表格分析)",        _test_table_analyzer),
    ("5",  "Format Converter (格式转换)",      _test_format_converter),
    ("6",  "Code Executor (代码执行)",         _test_code_executor),
    ("7",  "Text Summarizer (文本摘要)",       _test_text_summarizer),
    ("8",  "[Compound] Search→Read→Summarize", _test_compound_search_read),
    ("9",  "[Compound] Read→Analyze→Format",   _test_compound_analyze),
    ("a",  "[Compound] Calculate→Format",      _test_compound_calc),
    ("b",  "[Compound] Read→Summarize→Format", _test_compound_read_summarize),
]


def _interactive_mode(outdir: str | None = None) -> int:
    if outdir:
        print(f"\n  输出目录: {outdir}")
    while True:
        _print_header()
        for key, name, _ in MENU:
            print(f"  [{key}] {name}")
        print("  [0] 退出")
        choice = input("\n请选择: ").strip()

        if choice in ('0', 'q', 'quit', 'exit'):
            print("再见！")
            return 0

        for key, _, func in MENU:
            if choice == key:
                func(outdir)
                break
        else:
            print("无效选项")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.skill and args.input:
        return _cli_mode(args)
    else:
        # 尝试交互模式
        return _interactive_mode(args.outdir)


if __name__ == "__main__":
    raise SystemExit(main())
