"""Table Analyzer - 表格分析器，支持CSV/TSV/JSONL，含统计、质量检查和异常检测"""

from __future__ import annotations

import csv, json, statistics
from pathlib import Path

from skills import resolve_data_path
from skills.exceptions import (
    SkillError, InvalidFormatError, ParseError,
)
from skills.error_utils import (
    make_error_result, make_success_result, measure_latency,
    validate_file_exists, validate_file_extension, validate_non_negative_integer,
)

EXTS = {".csv", ".tsv", ".jsonl"}
DEF_PREVIEW = 5


def _read_csv_tsv(p: Path, d: str) -> tuple[list[str], list[dict]]:
    with p.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f, delimiter=d)
        if not r.fieldnames:
            raise ParseError(code="TANAL-EXEC-003", message="缺少表头行")
        return list(r.fieldnames), list(r)


def _read_jsonl(p: Path) -> tuple[list[str], list[dict]]:
    rows, cols = [], set()
    with p.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ParseError(code="TANAL-EXEC-002", message=f"第{i}行JSON解析失败") from exc
            if isinstance(row, dict):
                rows.append(row)
                cols.update(row.keys())
    return sorted(cols), rows


def _col_stats(vals: list) -> dict | None:
    try:
        nums = [float(v) if not isinstance(v, (int, float)) else float(v) for v in vals if str(v).strip()]
    except (ValueError, TypeError):
        return None
    if not nums:
        return None
    s = {"count": len(nums), "min": min(nums), "max": max(nums), "mean": statistics.fmean(nums)}
    if len(nums) >= 2:
        s["median"] = statistics.median(nums)
        s["stdev"] = statistics.stdev(nums)
    return s


def _quality(rows: list[dict], cols: list[str]) -> dict:
    n = len(rows)
    return {
        "total_rows": n, "total_columns": len(cols),
        "missing_values": {c: sum(1 for r in rows if not str(r.get(c, "")).strip()) for c in cols},
        "empty_rows": sum(1 for r in rows if all(not str(r.get(c, "")).strip() for c in cols)),
        "data_types": {c: ("numeric" if all(str(v).strip().replace(".","").replace("-","").isdigit()
                         for v in [r.get(c, "") for r in rows if str(r.get(c, "")).strip()]) else "string") for c in cols},
    }


def _outliers(rows: list[dict], cols: list[str]) -> dict:
    result = {}
    for c in cols:
        try:
            vals = sorted([float(v) if not isinstance(v, (int, float)) else float(v) for v in [r.get(c, "") for r in rows if str(r.get(c, "")).strip()]])
        except (ValueError, TypeError):
            continue
        if len(vals) < 4:
            continue
        q1, q3 = vals[len(vals) // 4], vals[3 * len(vals) // 4]
        lo, hi = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
        out = [v for v in vals if v < lo or v > hi]
        if out:
            result[c] = {"count": len(out), "lower_bound": lo, "upper_bound": hi}
    return result


def _viz(cols: list[str], stats: dict) -> list[str]:
    nc = [c for c in cols if c in stats]
    cc = [c for c in cols if c not in stats]
    sug = ["柱状图 - 数值分布", "折线图 - 趋势变化"]
    if len(nc) >= 2:
        sug.append("散点图 - 两列关系")
    if cc:
        sug.append("饼图 - 分类占比")
    if cc and nc:
        sug.append("分组柱状图 - 类别对比")
    return sug


def table_analyzer(path: str, max_rows_preview: int = DEF_PREVIEW, describe: bool = True,
                   check_quality: bool = False, detect_outliers: bool = False,
                   *, data_root: str | None = None) -> dict:
    input_data = {"path": path, "max_rows_preview": max_rows_preview}

    try:
        with measure_latency() as timer:
            validate_non_negative_integer(max_rows_preview, "max_rows_preview", "table_analyzer", "TANAL-VAL-002")
            source, root = resolve_data_path(path, data_root)
            validate_file_extension(source, EXTS, "path", "table_analyzer", "TANAL-VAL-003")
            validate_file_exists(source, "path", "table_analyzer", "TANAL-EXEC-001")

            sfx = source.suffix.lower()
            if sfx == ".csv":
                cols, rows = _read_csv_tsv(source, ",")
            elif sfx == ".tsv":
                cols, rows = _read_csv_tsv(source, "\t")
            else:
                cols, rows = _read_jsonl(source)

            stats = {}
            if describe:
                for c in cols:
                    s = _col_stats([r.get(c, "") for r in rows])
                    if s:
                        stats[c] = s

            output = {
                "path": source.relative_to(root).as_posix(),
                "num_rows": len(rows), "num_columns": len(cols),
                "columns": cols, "preview": rows[:max_rows_preview],
                "describe": stats, "visualization_suggestions": _viz(cols, stats),
            }
            if check_quality:
                output["quality"] = _quality(rows, cols)
            if detect_outliers:
                output["outliers"] = _outliers(rows, cols)

        return make_success_result("table_analyzer", input_data, output, timer.elapsed_ms)

    except SkillError as exc:
        return make_error_result("table_analyzer", exc, input_data)
    except Exception as exc:
        return make_error_result("table_analyzer", exc, input_data)
