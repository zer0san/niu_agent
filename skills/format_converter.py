"""Format Converter - 多格式文本转换器，支持 Markdown/JSON/YAML/CSV/HTML"""

from __future__ import annotations

import csv, io, json
from pathlib import Path

from skills.exceptions import SkillError, ParseError
from skills.error_utils import (
    make_error_result, make_success_result, measure_latency,
    validate_enum_value, validate_not_empty_string,
)

FORMATS = {"markdown", "json", "csv", "yaml", "html"}
OUT_DIR = Path(__file__).resolve().parents[1] / "outputs" / "format_converter_files"
NAMES = {"markdown": "converted.md", "json": "converted.json", "csv": "converted.csv",
         "yaml": "converted.yaml", "html": "converted.html"}
SUFFIXES = {k: f".{v.split('.')[-1]}" for k, v in NAMES.items()}


def _parse_kv(text: str) -> dict[str, str]:
    result = {}
    for i, line in enumerate((l.strip() for l in text.splitlines()), 1):
        if not line or ":" not in line:
            raise ParseError(code="FCONV-EXEC-002", message=f"第{i}行格式错误：缺少冒号",
                             details={"line": i})
        k, v = (p.strip() for p in line.split(":", 1))
        if not k or k in result:
            raise ParseError(code="FCONV-EXEC-003", message=f"无效或重复的key：{k}")
        result[k] = v
    if not result:
        raise ParseError(code="FCONV-EXEC-002", message="无可转换内容")
    return result


def _to_yaml(data: dict | list) -> str:
    if isinstance(data, dict):
        lines = []
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{k}:\n" + "\n".join(f"  {l}" for l in _to_yaml(v).split("\n")))
            else:
                lines.append(f"{k}: {v}")
        return "\n".join(lines)
    if isinstance(data, list):
        return "\n".join(f"- {i}" if not isinstance(i, (dict, list)) else f"- " + _to_yaml(i).replace("\n", "\n  ")
                         for i in data)
    return str(data)


def _to_csv(data: list[dict]) -> str:
    if not data:
        return ""
    o = io.StringIO()
    w = csv.DictWriter(o, fieldnames=list(data[0].keys()))
    w.writeheader()
    w.writerows(data)
    return o.getvalue()


def _to_html(data: list[dict]) -> str:
    if not data:
        return "<table></table>"
    hs = list(data[0].keys())
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return "\n".join([
        "<table>", "  <thead><tr>" + "".join(f"<th>{esc(h)}</th>" for h in hs) + "</tr></thead>",
        "  <tbody>"] + [
        "    <tr>" + "".join(f"<td>{esc(r.get(h, ''))}</td>" for h in hs) + "</tr>" for r in data
    ] + ["  </tbody>", "</table>"])


def _json_to_kv(data: dict) -> str:
    return "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v}"
                     for k, v in data.items())


def _output_path(d: str | None, fn: str | None, fmt: str) -> Path:
    directory = Path(d).resolve() if d else OUT_DIR.resolve()
    name = Path(fn).name if fn else NAMES[fmt]
    stem, suffix = Path(name).stem, SUFFIXES[fmt]
    candidate, i = directory / f"{stem}{suffix}", 1
    while candidate.exists():
        candidate = directory / f"{stem}({i}){suffix}"
        i += 1
    candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


def format_converter(text: str, target_format: str, output_filename: str | None = None,
                     output_dir: str | None = None, reverse: bool = False) -> dict:
    input_data = {"text": text[:100] + "..." if isinstance(text, str) and len(text) > 100 else text,
                  "target_format": target_format}

    try:
        with measure_latency() as timer:
            validate_not_empty_string(text, "text", "format_converter", "FCONV-VAL-002")
            fmt = target_format.strip().lower() if isinstance(target_format, str) else ""
            validate_enum_value(fmt, FORMATS, "target_format", "format_converter", "FCONV-VAL-003")

            parsed = None
            if fmt in ("json", "csv", "yaml", "html") or reverse:
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    try:
                        parsed = _parse_kv(text)
                    except ParseError:
                        if fmt == "json" and not reverse:
                            raise

            if fmt == "markdown":
                result = _json_to_kv(parsed) if reverse and parsed else \
                    "\n".join(f"- {l.strip()}" for l in text.splitlines() if l.strip())
            elif fmt == "json":
                if reverse:
                    if not (parsed and isinstance(parsed, dict)):
                        raise ParseError(code="FCONV-EXEC-001", message="反向转换需要JSON对象")
                    result = _json_to_kv(parsed)
                else:
                    result = json.dumps(parsed or _parse_kv(text), ensure_ascii=False, indent=2)
            elif fmt in ("csv", "html"):
                data = parsed if isinstance(parsed, list) else [parsed] if isinstance(parsed, dict) else None
                if not data:
                    raise ParseError(code="FCONV-EXEC-001", message=f"{fmt.upper()}需要JSON数组或对象")
                result = _to_csv(data) if fmt == "csv" else _to_html(data)
            elif fmt == "yaml":
                if not parsed:
                    raise ParseError(code="FCONV-EXEC-001", message="YAML需要JSON/key-value输入")
                result = _to_yaml(parsed)

            generated = _output_path(output_dir, output_filename, fmt)
            generated.write_text(result, encoding="utf-8")

            output = {"formatted_text": result, "generated_file_path": str(generated)}

        return make_success_result("format_converter", input_data, output, timer.elapsed_ms)

    except SkillError as exc:
        return make_error_result("format_converter", exc, input_data)
    except Exception as exc:
        return make_error_result("format_converter", exc, input_data)
