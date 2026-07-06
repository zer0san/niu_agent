"""File Reader - 安全读取本地文件，支持多编码和元数据"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from skills import resolve_data_path
from skills.exceptions import (
    SkillError, EncodingError, FileNotFoundError as SkillFileNotFoundError,
)
from skills.error_utils import (
    make_error_result, make_success_result, measure_latency,
    validate_file_exists, validate_file_extension, validate_positive_integer,
)

EXTENSIONS = {".txt", ".md", ".py", ".json", ".yaml", ".yml", ".csv"}
ENCODINGS = ["utf-8", "gbk", "latin-1", "cp1252"]
DEFAULT_MAX_CHARS = 2000


def _detect_encoding(path: Path) -> str:
    for enc in ENCODINGS:
        try:
            with open(path, "r", encoding=enc) as f:
                f.read(1024)
            return enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    return "utf-8"


def _format_size(b: int) -> str:
    for u in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"


def file_reader(path: str, max_chars: int = DEFAULT_MAX_CHARS, include_metadata: bool = False,
                 *, data_root: str | None = None) -> dict:
    input_data = {"path": path, "max_chars": max_chars}

    try:
        with measure_latency() as timer:
            validate_positive_integer(max_chars, "max_chars", "file_reader", "FREAD-VAL-002")
            source, root = resolve_data_path(path, data_root)
            validate_file_extension(source, EXTENSIONS, "path", "file_reader", "FREAD-VAL-003")
            validate_file_exists(source, "path", "file_reader", "FREAD-EXEC-001")

            try:
                enc = _detect_encoding(source)
                original = source.read_text(encoding=enc)
            except UnicodeDecodeError as exc:
                raise EncodingError(code="FREAD-EXEC-002", message=f"编码错误：{exc}",
                                    details={"path": str(source), "encoding": enc}) from exc

            content = original[:max_chars]
            output = {
                "content": content, "num_chars": len(content),
                "source": source.relative_to(root).as_posix(),
                "truncated": len(original) > len(content),
            }
            if include_metadata:
                stat = source.stat()
                output["metadata"] = {
                    "size_bytes": stat.st_size, "size_human": _format_size(stat.st_size),
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "encoding": enc,
                }

        return make_success_result("file_reader", input_data, output, timer.elapsed_ms)

    except SkillError as exc:
        return make_error_result("file_reader", exc, input_data)
    except Exception as exc:
        return make_error_result("file_reader", exc, input_data)
