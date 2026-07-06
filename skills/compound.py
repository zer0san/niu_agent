"""Compound Skills - 复合管道，链式调用多个基础Skill"""

from __future__ import annotations

from skills.calculator import calculator
from skills.file_reader import file_reader
from skills.local_file_search import local_file_search
from skills.table_analyzer import table_analyzer
from skills.text_summarizer import text_summarizer
from skills.format_converter import format_converter
from skills.code_executor import code_executor
from skills.error_utils import make_error_result, make_success_result, measure_latency
from skills.exceptions import SkillError


# ---- 管道1: 搜索 → 读取 → 摘要 ----

def search_read_summarize(query: str, root_dir: str = "docs", max_sentences: int = 3,
                          *, data_root: str | None = None) -> dict:
    """搜索文件 → 读取最佳匹配 → 生成摘要"""
    input_data = {"query": query, "root_dir": root_dir, "max_sentences": max_sentences}

    try:
        with measure_latency() as timer:
            # 1. 搜索
            sr = local_file_search(query, root_dir, top_k=1, data_root=data_root)
            if sr["status"] != "success":
                return make_error_result("compound:search_read_summarize",
                    SkillError("PIPE-SEARCH-FAIL", f"搜索失败: {sr['error']['message']}"), input_data)
            if not sr["output"]["results"]:
                return make_error_result("compound:search_read_summarize",
                    SkillError("PIPE-NO-MATCH", f"未找到匹配 '{query}' 的文件"), input_data)

            path = sr["output"]["results"][0]["path"]

            # 2. 读取
            fr = file_reader(path, data_root=data_root)
            if fr["status"] != "success":
                return make_error_result("compound:search_read_summarize",
                    SkillError("PIPE-READ-FAIL", f"读取失败: {fr['error']['message']}"), input_data)

            content = fr["output"]["content"]

            # 3. 摘要
            tr = text_summarizer(content, max_sentences=max_sentences)
            if tr["status"] != "success":
                return make_error_result("compound:search_read_summarize",
                    SkillError("PIPE-SUMM-FAIL", f"摘要失败: {tr['error']['message']}"), input_data)

            output = {"source": path, "content": content, "summary": tr["output"]["summary"],
                      "key_sentences": tr["output"]["key_sentences"],
                      "keywords": tr["output"]["keywords"], "stats": tr["output"]["stats"]}

        return make_success_result("compound:search_read_summarize", input_data, output, timer.elapsed_ms)

    except Exception as exc:
        return make_error_result("compound:search_read_summarize", exc, input_data)


# ---- 管道2: 读取表格 → 分析 → 格式化 ----

def read_analyze_format(path: str, target_format: str = "json",
                        *, data_root: str | None = None) -> dict:
    """读取表格文件 → 分析 → 格式化输出"""
    input_data = {"path": path, "target_format": target_format}

    try:
        with measure_latency() as timer:
            # 1. 分析表格
            tr = table_analyzer(path, describe=True, check_quality=True, data_root=data_root)
            if tr["status"] != "success":
                return make_error_result("compound:read_analyze_format",
                    SkillError("PIPE-ANALYZE-FAIL", f"分析失败: {tr['error']['message']}"), input_data)

            # 提取关键信息
            report_data = {
                "file": tr["output"]["path"],
                "rows": tr["output"]["num_rows"],
                "columns": tr["output"]["columns"],
                "statistics": tr["output"]["describe"],
                "visualization_suggestions": tr["output"]["visualization_suggestions"],
            }
            if "quality" in tr["output"]:
                report_data["quality"] = tr["output"]["quality"]

            # 2. 格式化
            import json as _json
            report_json = _json.dumps(report_data, ensure_ascii=False)
            fr = format_converter(report_json, target_format)

            output = {"report": fr.get("output", {}).get("formatted_text", report_json) if fr["status"] == "success" else _json.dumps(report_data, ensure_ascii=False, indent=2),
                      "data": report_data,
                      "source": tr["output"]["path"]}

        return make_success_result("compound:read_analyze_format", input_data, output, timer.elapsed_ms)

    except Exception as exc:
        return make_error_result("compound:read_analyze_format", exc, input_data)


# ---- 管道3: 计算 → 格式化结果 ----

def calculate_format(expression: str, target_format: str = "markdown") -> dict:
    """计算表达式 → 格式化结果"""
    input_data = {"expression": expression, "target_format": target_format}

    try:
        with measure_latency() as timer:
            # 1. 计算
            cr = calculator(expression)
            if cr["status"] != "success":
                return make_error_result("compound:calculate_format",
                    SkillError("PIPE-CALC-FAIL", f"计算失败: {cr['error']['message']}"), input_data)

            result = cr["output"]["result"]

            # 2. 格式化
            text = f"表达式: {expression}\n结果: {result}"
            fr = format_converter(text, target_format)

            output = {"expression": expression, "result": result,
                      "formatted": fr.get("output", {}).get("formatted_text", text) if fr["status"] == "success" else text}

        return make_success_result("compound:calculate_format", input_data, output, timer.elapsed_ms)

    except Exception as exc:
        return make_error_result("compound:calculate_format", exc, input_data)


# ---- 管道4: 读取 → 摘要 → 格式化报告 ----

def read_summarize_format(path: str, target_format: str = "markdown",
                          max_sentences: int = 3, *, data_root: str | None = None) -> dict:
    """读取文件 → 生成摘要 → 格式化报告"""
    input_data = {"path": path, "target_format": target_format, "max_sentences": max_sentences}

    try:
        with measure_latency() as timer:
            # 1. 读取
            fr = file_reader(path, data_root=data_root)
            if fr["status"] != "success":
                return make_error_result("compound:read_summarize_format",
                    SkillError("PIPE-READ-FAIL", f"读取失败: {fr['error']['message']}"), input_data)

            content = fr["output"]["content"]

            # 2. 摘要
            tr = text_summarizer(content, max_sentences=max_sentences)
            if tr["status"] != "success":
                return make_error_result("compound:read_summarize_format",
                    SkillError("PIPE-SUMM-FAIL", f"摘要失败: {tr['error']['message']}"), input_data)

            # 3. 格式化
            report = f"# 文件报告\n\n**文件**: {fr['output']['source']}\n\n## 摘要\n\n{tr['output']['summary']}\n\n## 关键词\n\n" + \
                     ", ".join(k["word"] for k in tr["output"]["keywords"])
            fr2 = format_converter(report, target_format)

            output = {"source": fr["output"]["source"], "summary": tr["output"]["summary"],
                      "keywords": [k["word"] for k in tr["output"]["keywords"]],
                      "report": fr2.get("output", {}).get("formatted_text", report) if fr2["status"] == "success" else report,
                      "stats": tr["output"]["stats"]}

        return make_success_result("compound:read_summarize_format", input_data, output, timer.elapsed_ms)

    except Exception as exc:
        return make_error_result("compound:read_summarize_format", exc, input_data)
