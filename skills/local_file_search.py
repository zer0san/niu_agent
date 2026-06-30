"""Local File Search - 基于关键词计分的本地文件搜索引擎，支持正则和排除模式"""

from __future__ import annotations

import re
from pathlib import Path

from skills import resolve_data_path
from skills.exceptions import (
    SkillError, InvalidFormatError, ParseError,
)
from skills.error_utils import (
    make_error_result, make_success_result, measure_latency,
    validate_directory_exists, validate_not_empty_string, validate_positive_integer,
)

EXTS = {".txt", ".md"}
DEF_RADIUS, DEF_TOP_K = 60, 5


def _snippet(text: str, terms: list[str], r: int = DEF_RADIUS) -> str:
    pos = [p for p in (text.lower().find(t.lower()) for t in terms) if p >= 0]
    if not pos:
        return text[:r * 2].replace("\n", " ").strip()
    start, end = max(0, min(pos) - r), min(len(text), min(pos) + r * 2)
    return ("..." if start else "") + text[start:end].replace("\n", " ").strip() + ("..." if end < len(text) else "")


def _find_matches(text: str, terms: list[str]) -> list[dict]:
    matches = []
    for term in terms:
        start, tl = 0, term.lower()
        while (p := text.lower().find(tl, start)) != -1:
            matches.append({"term": term, "start": p, "end": p + len(term)})
            start = p + 1
    return sorted(matches, key=lambda x: x["start"])


def local_file_search(query: str, root_dir: str = "docs", file_types: list[str] | None = None,
                      top_k: int = DEF_TOP_K, use_regex: bool = False,
                      exclude_patterns: list[str] | None = None, include_matches: bool = False,
                      *, data_root: str | None = None) -> dict:
    input_data = {"query": query, "root_dir": root_dir, "top_k": top_k, "use_regex": use_regex}

    try:
        with measure_latency() as timer:
            validate_not_empty_string(query, "query", "local_file_search", "FSEARCH-VAL-002")
            validate_positive_integer(top_k, "top_k", "local_file_search", "FSEARCH-VAL-003")

            search_root, data_root_path = resolve_data_path(root_dir, data_root)
            validate_directory_exists(search_root, "root_dir", "local_file_search", "FSEARCH-EXEC-001")

            exts = {f".{e.lower().lstrip('.')}" for e in (file_types or ["txt", "md"])}
            if not exts <= EXTS:
                raise InvalidFormatError(code="FSEARCH-VAL-004", message=f"不支持的类型：{exts - EXTS}")

            if use_regex:
                try:
                    re.compile(query)
                except re.error as exc:
                    raise ParseError(code="FSEARCH-EXEC-002", message=f"正则无效：{exc}") from exc
                terms, pattern = [query], query
            else:
                terms, pattern = [t for t in re.split(r"\s+", query.strip()) if t], None

            results = []
            for path in sorted(search_root.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in exts:
                    continue
                if any(p in str(path) for p in (exclude_patterns or [])):
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue

                score = len(re.findall(pattern, text, re.IGNORECASE)) if use_regex and pattern else \
                    sum(text.lower().count(t.lower()) for t in terms)
                if score:
                    r = {"path": path.relative_to(data_root_path).as_posix(), "score": score,
                         "snippet": _snippet(text, terms)}
                    if include_matches:
                        r["matches"] = _find_matches(text, terms)
                    results.append(r)

            results.sort(key=lambda x: (-x["score"], x["path"]))

        return make_success_result("local_file_search", input_data, {"results": results[:top_k]}, timer.elapsed_ms)

    except SkillError as exc:
        return make_error_result("local_file_search", exc, input_data)
    except Exception as exc:
        return make_error_result("local_file_search", exc, input_data)
