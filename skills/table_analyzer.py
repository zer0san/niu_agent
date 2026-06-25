from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path
from time import perf_counter
from typing import Optional

from skills import resolve_data_path
from skills.exceptions import (
    SkillError,
    InputTypeError,
    InputValueError,
    FileNotFoundError as SkillFileNotFoundError,
    InvalidFormatError,
    PathEscapeError,
    ParseError,
)
from skills.error_utils import (
    make_error_result,
    make_success_result,
    validate_type,
    validate_non_negative_integer,
    validate_path_not_escape,
    validate_file_exists,
    validate_file_extension,
    measure_latency,
)


# 支持的文件类型
SUPPORTED_EXTENSIONS = {".csv", ".tsv", ".jsonl"}

# 默认配置
DEFAULT_MAX_ROWS_PREVIEW = 5


def _read_csv_tsv(file_path: Path, delimiter: str) -> tuple[list[str], list[dict]]:
    """
    读取CSV/TSV文件

    Args:
        file_path: 文件路径
        delimiter: 分隔符

    Returns:
        (列名列表, 行数据列表)
    """
    with file_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if not reader.fieldnames:
            raise ParseError(
                code="TANAL-EXEC-003",
                message="表格缺少表头行",
                details={"path": str(file_path)},
                suggestion="请确保CSV/TSV文件的第一行是表头"
            )
        rows = list(reader)
        columns = list(reader.fieldnames)
    return columns, rows


def _read_jsonl(file_path: Path) -> tuple[list[str], list[dict]]:
    """
    读取JSON Lines文件

    Args:
        file_path: 文件路径

    Returns:
        (列名列表, 行数据列表)
    """
    rows = []
    columns = set()

    with file_path.open("r", encoding="utf-8") as handle:
        for line_num, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
                    columns.update(row.keys())
                else:
                    raise ParseError(
                        code="TANAL-EXEC-002",
                        message=f"第{line_num}行不是JSON对象",
                        details={"line": line_num, "content": line[:100]},
                        suggestion="请确保每行都是有效的JSON对象"
                    )
            except json.JSONDecodeError as exc:
                raise ParseError(
                    code="TANAL-EXEC-002",
                    message=f"第{line_num}行JSON解析失败：{exc}",
                    details={"line": line_num, "error": str(exc)},
                    suggestion="请确保每行都是有效的JSON格式"
                ) from exc

    return sorted(columns), rows


def _read_table(file_path: Path) -> tuple[list[str], list[dict]]:
    """
    读取表格文件（根据扩展名选择读取方式）

    Args:
        file_path: 文件路径

    Returns:
        (列名列表, 行数据列表)
    """
    suffix = file_path.suffix.lower()

    if suffix == ".csv":
        return _read_csv_tsv(file_path, ",")
    elif suffix == ".tsv":
        return _read_csv_tsv(file_path, "\t")
    elif suffix == ".jsonl":
        return _read_jsonl(file_path)
    else:
        raise InvalidFormatError(
            code="TANAL-VAL-003",
            message=f"不支持的文件类型：{suffix}",
            details={"extension": suffix, "supported": list(SUPPORTED_EXTENSIONS)},
            suggestion=f"请使用以下文件类型：{SUPPORTED_EXTENSIONS}"
        )


def _calculate_column_stats(values: list[str]) -> Optional[dict]:
    """
    计算列的统计信息

    Args:
        values: 列值列表

    Returns:
        统计信息字典，如果不是数值列则返回None
    """
    # 过滤空值
    non_empty = [v.strip() for v in values if v.strip()]

    if not non_empty:
        return None

    # 尝试转换为数值
    try:
        numeric_values = [float(v) for v in non_empty]
    except ValueError:
        return None

    # 计算统计信息
    stats = {
        "count": len(numeric_values),
        "min": min(numeric_values),
        "max": max(numeric_values),
        "mean": statistics.fmean(numeric_values),
    }

    # 计算中位数和标准差（如果有足够数据）
    if len(numeric_values) >= 2:
        stats["median"] = statistics.median(numeric_values)
        stats["stdev"] = statistics.stdev(numeric_values)

    return stats


def _check_data_quality(rows: list[dict], columns: list[str]) -> dict:
    """
    检查数据质量

    Args:
        rows: 行数据列表
        columns: 列名列表

    Returns:
        数据质量报告
    """
    total_rows = len(rows)
    quality = {
        "total_rows": total_rows,
        "total_columns": len(columns),
        "missing_values": {},
        "missing_percentage": {},
        "empty_rows": 0,
        "data_types": {},
    }

    # 统计每列的缺失值
    for column in columns:
        missing = sum(1 for row in rows if not row.get(column, "").strip())
        quality["missing_values"][column] = missing
        quality["missing_percentage"][column] = round(missing / total_rows * 100, 2) if total_rows > 0 else 0

    # 统计空行
    quality["empty_rows"] = sum(
        1 for row in rows
        if all(not row.get(col, "").strip() for col in columns)
    )

    # 检测数据类型
    for column in columns:
        values = [row.get(column, "").strip() for row in rows if row.get(column, "").strip()]
        if not values:
            quality["data_types"][column] = "empty"
            continue

        # 检查是否为数值
        try:
            [float(v) for v in values]
            quality["data_types"][column] = "numeric"
            continue
        except ValueError:
            pass

        # 检查是否为布尔值
        bool_values = {"true", "false", "yes", "no", "1", "0"}
        if all(v.lower() in bool_values for v in values):
            quality["data_types"][column] = "boolean"
            continue

        quality["data_types"][column] = "string"

    return quality


def _detect_outliers(rows: list[dict], columns: list[str]) -> dict:
    """
    检测异常值

    Args:
        rows: 行数据列表
        columns: 列名列表

    Returns:
        异常值报告
    """
    outliers = {}

    for column in columns:
        values = [row.get(column, "").strip() for row in rows if row.get(column, "").strip()]
        if not values:
            continue

        try:
            numeric_values = [float(v) for v in values]
        except ValueError:
            continue

        # 使用IQR方法检测异常值
        if len(numeric_values) < 4:
            continue

        sorted_values = sorted(numeric_values)
        q1 = sorted_values[len(sorted_values) // 4]
        q3 = sorted_values[3 * len(sorted_values) // 4]
        iqr = q3 - q1
        lower_bound = q1 - 1.5 * iqr
        upper_bound = q3 + 1.5 * iqr

        outlier_values = [v for v in numeric_values if v < lower_bound or v > upper_bound]
        if outlier_values:
            outliers[column] = {
                "count": len(outlier_values),
                "values": outlier_values[:5],  # 最多显示5个
                "lower_bound": lower_bound,
                "upper_bound": upper_bound,
            }

    return outliers


def _suggest_visualization(columns: list[str], stats: dict) -> list[str]:
    """
    推荐可视化图表类型

    Args:
        columns: 列名列表
        stats: 统计信息

    Returns:
        推荐的图表类型列表
    """
    suggestions = []
    numeric_cols = [col for col in columns if col in stats]
    categorical_cols = [col for col in columns if col not in stats]

    # 如果有数值列
    if numeric_cols:
        suggestions.append("柱状图 - 展示数值分布")
        suggestions.append("折线图 - 展示趋势变化")

        # 如果有多个数值列
        if len(numeric_cols) >= 2:
            suggestions.append("散点图 - 展示两列关系")

    # 如果有分类列
    if categorical_cols:
        suggestions.append("饼图 - 展示分类占比")

    # 如果同时有分类和数值列
    if categorical_cols and numeric_cols:
        suggestions.append("分组柱状图 - 按类别对比数值")

    return suggestions


def table_analyzer(
    path: str,
    max_rows_preview: int = DEFAULT_MAX_ROWS_PREVIEW,
    describe: bool = True,
    check_quality: bool = False,
    detect_outliers: bool = False,
    *,
    data_root: str | None = None,
) -> dict:
    """
    分析表格文件（增强版）

    Args:
        path: 表格文件路径（相对于data目录）
        max_rows_preview: 预览行数
        describe: 是否计算数值列统计
        check_quality: 是否检查数据质量
        detect_outliers: 是否检测异常值
        data_root: 数据根目录（自动注入）

    Returns:
        包含表格分析结果或错误的字典

    Examples:
        >>> table_analyzer("tables/results.csv")
        {'skill_name': 'table_analyzer', 'status': 'success', 'input': {...}, 'output': {'path': '...', 'num_rows': 3, ...}, 'error': None, 'latency_ms': 1.2}

        >>> table_analyzer("tables/results.csv", check_quality=True)
        {'skill_name': 'table_analyzer', 'status': 'success', 'input': {...}, 'output': {'path': '...', 'num_rows': 3, ..., 'quality': {...}}, 'error': None, 'latency_ms': 1.5}
    """
    input_data = {
        "path": path,
        "max_rows_preview": max_rows_preview,
        "describe": describe,
        "check_quality": check_quality,
        "detect_outliers": detect_outliers,
    }

    try:
        with measure_latency() as timer:
            # 验证max_rows_preview
            validate_non_negative_integer(
                max_rows_preview, "max_rows_preview", "table_analyzer", "TANAL-VAL-002"
            )

            # 解析路径（resolve_data_path会检查路径逃逸）
            source, root = resolve_data_path(path, data_root)

            # 验证文件类型
            validate_file_extension(
                source, SUPPORTED_EXTENSIONS, "path", "table_analyzer", "TANAL-VAL-003"
            )

            # 验证文件存在
            validate_file_exists(source, "path", "table_analyzer", "TANAL-EXEC-001")

            # 读取表格
            columns, rows = _read_table(source)

            # 计算统计信息
            stats = {}
            if describe:
                for column in columns:
                    column_values = [row.get(column, "") for row in rows]
                    column_stats = _calculate_column_stats(column_values)
                    if column_stats:
                        stats[column] = column_stats

            # 构建输出
            output = {
                "path": source.relative_to(root).as_posix(),
                "num_rows": len(rows),
                "num_columns": len(columns),
                "columns": columns,
                "preview": rows[:max_rows_preview],
                "describe": stats,
            }

            # 添加数据质量检查（可选）
            if check_quality:
                output["quality"] = _check_data_quality(rows, columns)

            # 添加异常值检测（可选）
            if detect_outliers:
                output["outliers"] = _detect_outliers(rows, columns)

            # 添加可视化建议
            output["visualization_suggestions"] = _suggest_visualization(columns, stats)

            return make_success_result(
                "table_analyzer",
                input_data,
                output,
                timer.elapsed_ms
            )

    except SkillError as exc:
        return make_error_result("table_analyzer", exc, input_data, timer.elapsed_ms if 'timer' in dir() else 0.0)

    except Exception as exc:
        return make_error_result("table_analyzer", exc, input_data, timer.elapsed_ms if 'timer' in dir() else 0.0)
    manual_test()
