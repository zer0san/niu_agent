from __future__ import annotations

import re
import json
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
    validate_not_empty_string,
    validate_positive_integer,
    validate_path_not_escape,
    validate_directory_exists,
    validate_file_extension,
    measure_latency,
)


# 支持的文件类型
SUPPORTED_EXTENSIONS = {".txt", ".md"}

# 默认配置
DEFAULT_TOP_K = 5
DEFAULT_SNIPPET_RADIUS = 60
MAX_SNIPPET_LENGTH = 200


def _snippet(text: str, terms: list[str], radius: int = DEFAULT_SNIPPET_RADIUS) -> str:
    """
    生成搜索结果片段

    Args:
        text: 原始文本
        terms: 搜索关键词列表
        radius: 片段半径（前后各多少字符）

    Returns:
        包含关键词的上下文片段
    """
    lowered = text.casefold()
    positions = [lowered.find(term.casefold()) for term in terms]
    positions = [position for position in positions if position >= 0]

    if not positions:
        return text[:radius * 2].replace("\n", " ").strip()

    start = max(0, min(positions) - radius)
    end = min(len(text), start + radius * 2)
    prefix = "..." if start else ""
    suffix = "..." if end < len(text) else ""

    return prefix + text[start:end].replace("\n", " ").strip() + suffix


def _find_matches(text: str, terms: list[str]) -> list[dict]:
    """
    查找所有匹配位置

    Args:
        text: 原始文本
        terms: 搜索关键词列表

    Returns:
        匹配位置列表
    """
    matches = []
    lowered = text.casefold()

    for term in terms:
        term_lower = term.casefold()
        start = 0
        while True:
            pos = lowered.find(term_lower, start)
            if pos == -1:
                break
            matches.append({
                "term": term,
                "start": pos,
                "end": pos + len(term),
                "matched_text": text[pos:pos + len(term)]
            })
            start = pos + 1

    # 按位置排序
    matches.sort(key=lambda x: x["start"])
    return matches


def _regex_search(text: str, pattern: str) -> list[dict]:
    """
    正则表达式搜索

    Args:
        text: 原始文本
        pattern: 正则表达式模式

    Returns:
        匹配结果列表
    """
    matches = []
    try:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            matches.append({
                "start": match.start(),
                "end": match.end(),
                "matched_text": match.group(),
                "groups": match.groups()
            })
    except re.error as exc:
        raise ParseError(
            code="FSEARCH-EXEC-002",
            message=f"正则表达式无效：{exc}",
            details={"pattern": pattern, "error": str(exc)},
            suggestion="请检查正则表达式语法"
        ) from exc
    return matches


def _calculate_score(text: str, terms: list[str], use_regex: bool = False, pattern: str = None) -> int:
    """
    计算匹配分数

    Args:
        text: 原始文本
        terms: 搜索关键词列表
        use_regex: 是否使用正则表达式
        pattern: 正则表达式模式

    Returns:
        匹配分数

    Raises:
        ParseError: 正则表达式无效时抛出
    """
    if use_regex and pattern:
        try:
            matches = re.findall(pattern, text, re.IGNORECASE)
            return len(matches)
        except re.error as exc:
            raise ParseError(
                code="FSEARCH-EXEC-002",
                message=f"正则表达式无效：{exc}",
                details={"pattern": pattern, "error": str(exc)},
                suggestion="请检查正则表达式语法"
            ) from exc
    else:
        lowered = text.casefold()
        return sum(lowered.count(term.casefold()) for term in terms)


def _should_exclude(path: Path, exclude_patterns: list[str] = None) -> bool:
    """
    检查是否应该排除该路径

    Args:
        path: 文件路径
        exclude_patterns: 排除模式列表

    Returns:
        是否应该排除
    """
    if not exclude_patterns:
        return False

    path_str = str(path)
    for pattern in exclude_patterns:
        if pattern in path_str:
            return True
    return False


def local_file_search(
    query: str,
    root_dir: str = "docs",
    file_types: list[str] | None = None,
    top_k: int = DEFAULT_TOP_K,
    use_regex: bool = False,
    exclude_patterns: list[str] | None = None,
    include_matches: bool = False,
    *,
    data_root: str | None = None,
) -> dict:
    """
    搜索本地文件内容（增强版）

    Args:
        query: 搜索关键词（空格分隔）或正则表达式
        root_dir: 搜索根目录（相对于data目录）
        file_types: 文件类型过滤
        top_k: 返回结果数量
        use_regex: 是否使用正则表达式搜索
        exclude_patterns: 排除模式列表（包含该模式的路径将被排除）
        include_matches: 是否包含匹配位置详情
        data_root: 数据根目录（自动注入）

    Returns:
        包含搜索结果或错误的字典

    Examples:
        >>> local_file_search("Agent 工具")
        {'skill_name': 'local_file_search', 'status': 'success', 'input': {...}, 'output': {'results': [...]}, 'error': None, 'latency_ms': 5.2}

        >>> local_file_search("Agent.*工具", use_regex=True)
        {'skill_name': 'local_file_search', 'status': 'success', 'input': {...}, 'output': {'results': [...]}, 'error': None, 'latency_ms': 5.2}
    """
    input_data = {
        "query": query,
        "root_dir": root_dir,
        "file_types": file_types,
        "top_k": top_k,
        "use_regex": use_regex,
        "exclude_patterns": exclude_patterns,
    }

    try:
        with measure_latency() as timer:
            # 验证查询
            validate_not_empty_string(
                query, "query", "local_file_search", "FSEARCH-VAL-002"
            )

            # 验证top_k
            validate_positive_integer(
                top_k, "top_k", "local_file_search", "FSEARCH-VAL-003"
            )

            # 解析搜索根目录（resolve_data_path会检查路径逃逸）
            search_root, data_root_path = resolve_data_path(root_dir, data_root)

            # 验证目录存在
            validate_directory_exists(
                search_root, "root_dir", "local_file_search", "FSEARCH-EXEC-001"
            )

            # 处理文件类型
            extensions = file_types or ["txt", "md"]
            normalized_extensions = {f".{item.lower().lstrip('.')}" for item in extensions}

            # 验证文件类型
            if not normalized_extensions.issubset(SUPPORTED_EXTENSIONS):
                raise InvalidFormatError(
                    code="FSEARCH-VAL-004",
                    message=f"不支持的文件类型：{normalized_extensions - SUPPORTED_EXTENSIONS}",
                    details={
                        "requested_types": list(normalized_extensions),
                        "supported_types": list(SUPPORTED_EXTENSIONS)
                    },
                    suggestion=f"请使用以下文件类型：{SUPPORTED_EXTENSIONS}"
                )

            # 分词（非正则模式）
            if use_regex:
                terms = [query]
                pattern = query
            else:
                terms = [term for term in re.split(r"\s+", query.strip()) if term]
                pattern = None

            # 搜索文件
            results = []
            for path in sorted(search_root.rglob("*")):
                # 跳过非文件
                if not path.is_file():
                    continue

                # 跳过不支持的文件类型
                if path.suffix.lower() not in normalized_extensions:
                    continue

                # 跳过排除的路径
                if _should_exclude(path, exclude_patterns):
                    continue

                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue  # 跳过无法读取的文件

                # 计算分数
                score = _calculate_score(text, terms, use_regex, pattern)

                if score:
                    result = {
                        "path": path.relative_to(data_root_path).as_posix(),
                        "score": score,
                        "snippet": _snippet(text, terms),
                    }

                    # 添加匹配详情（可选）
                    if include_matches:
                        if use_regex:
                            result["matches"] = _regex_search(text, pattern)
                        else:
                            result["matches"] = _find_matches(text, terms)

                    results.append(result)

            # 按分数降序排序
            results.sort(key=lambda item: (-item["score"], item["path"]))

            return make_success_result(
                "local_file_search",
                input_data,
                {"results": results[:top_k]},
                timer.elapsed_ms
            )

    except SkillError as exc:
        return make_error_result("local_file_search", exc, input_data, timer.elapsed_ms if 'timer' in dir() else 0.0)

    except Exception as exc:
        return make_error_result("local_file_search", exc, input_data, timer.elapsed_ms if 'timer' in dir() else 0.0)


if __name__ == "__main__":
    manual_test()
