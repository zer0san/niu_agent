from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from common.io_utils import append_jsonl, read_json, read_text, read_yaml, write_json, write_text
from common.logging_utils import now_iso
from common.path_utils import resolve_cli_path, resolve_from_file

_EMBEDDING_MODEL_CACHE = {}
_STOPWORDS_CACHE = {}
_JIEBA_USERDICT_CACHE = set()
_KEYWORD_SCHEMA_VERSION = "1"


def _memory_paths(config_path: str | Path) -> dict[str, Path | int]:
    # 读取 memory.yaml 中的基础路径配置，供“按 memory id 查找”和“保存 Markdown 记忆”使用。
    path = Path(config_path).resolve()
    config = read_yaml(path)
    if not isinstance(config, dict) or not isinstance(config.get("memory"), dict):
        raise ValueError("memory.yaml must define a memory object")
    memory = config["memory"]
    required = ["root_dir", "global_memory_dir", "conversation_memory_dir", "index_path", "max_memory_chars"]
    missing = [name for name in required if name not in memory]
    if missing:
        raise ValueError(f"memory.yaml missing: {', '.join(missing)}")
    root = resolve_from_file(memory["root_dir"], path)
    max_chars = memory["max_memory_chars"]
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars <= 0:
        raise ValueError("max_memory_chars must be a positive integer")
    return {
        "root": root,
        "global": root / memory["global_memory_dir"],
        "conversations": root / memory["conversation_memory_dir"],
        "index": root / memory["index_path"],
        "max_chars": max_chars,
    }


def get_history_compress_config(config_path: str | Path) -> dict:
    path = Path(config_path).resolve()
    config = read_yaml(path)
    if not isinstance(config, dict) or not isinstance(config.get("memory"), dict):
        raise ValueError("memory.yaml must define a memory object")
    memory = config["memory"]
    max_chars = memory.get("max_memory_chars", 2000)
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars <= 0:
        max_chars = 2000
    threshold = int(max_chars * 0.8)
    keep_recent = memory.get("history_compress_keep_recent", 2)
    max_sentences = memory.get("history_compress_max_sentences", 3)
    enabled = memory.get("enable_history_compress", True)

    if not isinstance(keep_recent, int) or isinstance(keep_recent, bool) or keep_recent < 0:
        keep_recent = 2
    if not isinstance(max_sentences, int) or isinstance(max_sentences, bool) or max_sentences < 1:
        max_sentences = 3
    if not isinstance(enabled, bool):
        enabled = True

    return {
        "history_compress_keep_recent": keep_recent,
        "history_compress_max_sentences": max_sentences,
        "enable_history_compress": enabled,
    }


def _vector_settings(config_path: str | Path) -> dict:
    # Optional vector memory settings. Milvus is imported only when vector memory is enabled.
    path = Path(config_path).resolve()
    config = read_yaml(path)
    vector = config.get("vector_memory", {}) if isinstance(config, dict) else {}
    if not isinstance(vector, dict):
        vector = {}
    return {
        "enabled": bool(vector.get("enabled", False)),
        "backend": vector.get("backend", "milvus"),
        "db_path": vector.get("db_path", "../memory/milvus_memory.db"),
        "collection_name": vector.get("collection_name", "memory_chunks"),
        "embedding_model_path": vector.get("embedding_model_path", "../embedding_models/bge-small-zh-v1.5"),
        "embedding_dim": int(vector.get("embedding_dim", 512)),
        "query_instruction": vector.get("query_instruction", "为这个句子生成表示以用于检索相关文章："),
        "top_k": int(vector.get("top_k", 3)),
        "candidate_k": int(vector.get("candidate_k", max(int(vector.get("top_k", 3)) * 4, 20))),
        "chunk_size": int(vector.get("chunk_size", 500)),
        "chunk_overlap": int(vector.get("chunk_overlap", 80)),
        "title_match_boost": float(vector.get("title_match_boost", 0.05)),
    }


def _keyword_settings(config_path: str | Path) -> dict:
    path = Path(config_path).resolve()
    config = read_yaml(path)
    keyword = config.get("keyword_memory", {}) if isinstance(config, dict) else {}
    if not isinstance(keyword, dict):
        keyword = {}
    return {
        "enabled": bool(keyword.get("enabled", False)),
        "backend": keyword.get("backend", "sqlite_bm25"),
        "index_path": keyword.get("index_path", "keyword_bm25.sqlite"),
        "stopwords_path": keyword.get("stopwords_path", "../memory/stopwords_baidu.txt"),
        "userdict_path": keyword.get("userdict_path"),
        "top_k": int(keyword.get("top_k", 3)),
        "candidate_k": int(keyword.get("candidate_k", max(int(keyword.get("top_k", 3)) * 4, 20))),
        "chunk_size": int(keyword.get("chunk_size", 500)),
        "chunk_overlap": int(keyword.get("chunk_overlap", 80)),
        "bm25_k1": float(keyword.get("bm25_k1", 1.5)),
        "bm25_b": float(keyword.get("bm25_b", 0.75)),
    }


def _fusion_settings(config_path: str | Path) -> dict:
    path = Path(config_path).resolve()
    config = read_yaml(path)
    fusion = config.get("retrieval_fusion", {}) if isinstance(config, dict) else {}
    if not isinstance(fusion, dict):
        fusion = {}
    keyword = _keyword_settings(config_path)
    vector = _vector_settings(config_path)
    return {
        "enabled": bool(fusion.get("enabled", True)),
        "strategy": fusion.get("strategy", "rrf"),
        "final_top_k": int(fusion.get("final_top_k", max(keyword["top_k"], vector["top_k"]))),
        "rrf_k": int(fusion.get("rrf_k", 60)),
        "keyword_weight": float(fusion.get("keyword_weight", 1.0)),
        "vector_weight": float(fusion.get("vector_weight", 1.0)),
        "dedupe_by_memory_id": bool(fusion.get("dedupe_by_memory_id", True)),
        "title_match_boost": float(fusion.get("title_match_boost", 0.05)),
    }


def _summary_settings(config_path: str | Path) -> dict:
    path = Path(config_path).resolve()
    config = read_yaml(path)
    summary = config.get("memory_summary", {}) if isinstance(config, dict) else {}
    if not isinstance(summary, dict):
        summary = {}
    return {
        "enabled": bool(summary.get("enabled", True)),
        "max_source_chars": int(summary.get("max_source_chars", 800)),
        "max_summary_chars": int(summary.get("max_summary_chars", 500)),
        "max_sentences": int(summary.get("max_sentences", 3)),
        "max_keywords": int(summary.get("max_keywords", 8)),
    }


def _load_stopwords(config_path: str | Path) -> set[str]:
    settings = _keyword_settings(config_path)
    config_file = Path(config_path).resolve()
    stopwords_path = resolve_from_file(settings["stopwords_path"], config_file)
    cache_key = str(stopwords_path)
    if cache_key in _STOPWORDS_CACHE:
        return _STOPWORDS_CACHE[cache_key]
    stopwords = {"user", "assistant", "system", "role", "content"}
    if stopwords_path.is_file():
        content = None
        for encoding in ("utf-8", "utf-8-sig", "gb18030"):
            try:
                content = stopwords_path.read_text(encoding=encoding)
                break
            except UnicodeDecodeError:
                continue
        if content is None:
            content = stopwords_path.read_text(encoding="utf-8", errors="ignore")
        for line in content.splitlines():
            word = line.strip().lower()
            if word:
                stopwords.add(word)
    _STOPWORDS_CACHE[cache_key] = stopwords
    return stopwords


def _ensure_jieba(config_path: str | Path | None = None):
    try:
        import jieba
    except ImportError as exc:
        raise ImportError("jieba is required for keyword_memory backend sqlite_bm25") from exc

    if config_path is not None:
        settings = _keyword_settings(config_path)
        userdict_path = settings.get("userdict_path")
        if isinstance(userdict_path, str) and userdict_path.strip():
            resolved = resolve_from_file(userdict_path, Path(config_path).resolve())
            cache_key = str(resolved)
            if resolved.is_file() and cache_key not in _JIEBA_USERDICT_CACHE:
                jieba.load_userdict(str(resolved))
                _JIEBA_USERDICT_CACHE.add(cache_key)
    return jieba


def _keyword_terms(
    text: str,
    stopwords: set[str] | None = None,
    config_path: str | Path | None = None,
) -> list[str]:
    if not text:
        return []
    jieba = _ensure_jieba(config_path)
    stopwords = stopwords or set()
    terms = []
    for match in re.finditer(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", text.lower()):
        token = match.group(0)
        if re.fullmatch(r"[A-Za-z0-9_]+", token):
            if len(token) > 1 and token not in stopwords:
                terms.append(token)
            continue
        chinese_terms = []
        for segment in jieba.cut(token, cut_all=False):
            term = segment.strip().lower()
            if len(term) > 1 and term not in stopwords:
                chinese_terms.append(term)
        terms.extend(chinese_terms)
    return terms

## 把json字符串解析成python对象
def _extract_json_markdown_section(markdown: str, heading: str) -> object | None:
    pattern = rf"##\s+{re.escape(heading)}\s*```json\s*(.*?)\s*```"
    match = re.search(pattern, markdown, flags=re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _extract_plain_markdown_section(markdown: str, heading: str) -> str:
    pattern = rf"##\s+{re.escape(heading)}\s*(.*?)(?=\n##\s+|\Z)"
    match = re.search(pattern, markdown, flags=re.DOTALL)
    return match.group(1).strip() if match else ""

##只保留user和assistant的内容
def _messages_to_memory_text(messages: list[dict]) -> str:
    lines = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            lines.append(content.strip())
    return "\n\n".join(lines)


def _extract_conversation_messages(markdown: str) -> str:
    messages = _extract_json_markdown_section(markdown, "Messages")
    if not isinstance(messages, list):
        return ""
    return _messages_to_memory_text(messages)


def _split_summary_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    parts = re.split(r"(?<=[\u3002\uff01\uff1f\uff1b])\s*|(?<=[.!?;])\s+", normalized)
    sentences = [part.strip() for part in parts if part and part.strip()]
    return sentences or [normalized]


def _summarize_memory_content(text: str, config_path: str | Path) -> dict:
    settings = _summary_settings(config_path)
    clean_text = re.sub(r"\s+", " ", text).strip()
    if not settings["enabled"] or len(clean_text) <= settings["max_source_chars"]:
        return {
            "summary": clean_text[: settings["max_summary_chars"]],
            "summarized": False,
            "keywords": [],
            "source_chars": len(clean_text),
            "summary_chars": min(len(clean_text), settings["max_summary_chars"]),
        }

    sentences = _split_summary_sentences(clean_text)
    stopwords = _load_stopwords(config_path)
    terms = _keyword_terms(clean_text, stopwords)
    term_freq = Counter(terms)
    sentence_scores = []
    for index, sentence in enumerate(sentences):
        sentence_terms = _keyword_terms(sentence, stopwords)
        if not sentence_terms:
            score = 0.0
        else:
            score = sum(term_freq.get(term, 0) for term in sentence_terms) / len(sentence_terms)
        sentence_scores.append({"index": index, "sentence": sentence, "score": score})

    selected = sorted(sentence_scores, key=lambda item: item["score"], reverse=True)[: settings["max_sentences"]]
    selected.sort(key=lambda item: item["index"])
    summary_parts = []
    used = 0
    for item in selected:
        sentence = item["sentence"]
        if used + len(sentence) > settings["max_summary_chars"]:
            remaining = settings["max_summary_chars"] - used
            if remaining > 0:
                summary_parts.append(sentence[:remaining])
            break
        summary_parts.append(sentence)
        used += len(sentence)
    summary_text = "\n".join(part for part in summary_parts if part).strip()
    if not summary_text:
        summary_text = clean_text[: settings["max_summary_chars"]]
    keywords = [
        {"word": word, "count": count}
        for word, count in term_freq.most_common(settings["max_keywords"])
    ]
    return {
        "summary": summary_text,
        "summarized": True,
        "keywords": keywords,
        "source_chars": len(clean_text),
        "summary_chars": len(summary_text),
    }


def _memory_content_for_use(metadata: dict, raw_content: str) -> str:
    merged_content = _extract_plain_markdown_section(raw_content, "Merged Memory")
    if merged_content:
        return merged_content
    summary_content = _extract_plain_markdown_section(raw_content, "Memory Summary")
    if summary_content:
        return summary_content
    if metadata.get("memory_type") == "conversation":
        messages_content = _extract_conversation_messages(raw_content)
        if messages_content:
            return messages_content
    return raw_content


def _memory_document_from_index(paths: dict[str, Path | int], index: dict, memory_id: str) -> tuple[dict | None, dict | None]:
    metadata = index.get(memory_id)
    if not isinstance(metadata, dict):
        return None, {"memory_id": memory_id, "type": "MemoryNotFound", "message": "memory_id does not exist"}
    relative_path = metadata.get("path")
    if not isinstance(relative_path, str):
        return None, {"memory_id": memory_id, "type": "InvalidMetadata", "message": "memory path is missing"}
    document_path = (paths["root"] / relative_path).resolve()
    try:
        document_path.relative_to(paths["root"].resolve())
    except ValueError:
        return None, {"memory_id": memory_id, "type": "InvalidPath", "message": "memory path escapes root"}
    if not document_path.is_file():
        return None, {"memory_id": memory_id, "type": "FileNotFoundError", "message": f"memory file not found: {relative_path}"}
    raw_content = read_text(document_path)
    return {
        "memory_id": memory_id,
        "metadata": metadata,
        "path": relative_path,
        "content": _memory_content_for_use(metadata, raw_content),
        "raw_content": raw_content,
    }, None


def _milvus_client_for_db(config_path: str | Path, db_path_value: str):
    from pymilvus import MilvusClient

    config_file = Path(config_path).resolve()
    db_path = resolve_from_file(db_path_value, config_file)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return MilvusClient(uri=str(db_path))


def _load_milvus_collection(client, collection_name: str) -> None:
    # Milvus Lite may release collections between runs; search requires an explicit load.
    client.load_collection(collection_name=collection_name)


def _keyword_sqlite_path(config_path: str | Path, paths: dict[str, Path | int] | None = None) -> Path:
    settings = _keyword_settings(config_path)
    index_path = Path(settings["index_path"])
    if index_path.is_absolute():
        return index_path
    memory_root = paths["root"] if paths is not None else _memory_paths(config_path)["root"]
    return (memory_root / index_path).resolve()


def _keyword_sqlite_connect(config_path: str | Path, paths: dict[str, Path | int] | None = None) -> sqlite3.Connection:
    db_path = _keyword_sqlite_path(config_path, paths)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def _reset_keyword_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        DROP TABLE IF EXISTS postings;
        DROP TABLE IF EXISTS terms;
        DROP TABLE IF EXISTS chunks;
        DROP TABLE IF EXISTS stats;
        """
    )


def _ensure_keyword_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            memory_id TEXT NOT NULL,
            memory_type TEXT,
            conversation_id TEXT,
            title TEXT,
            path TEXT,
            content TEXT,
            chunk_index INTEGER NOT NULL,
            chunk_len INTEGER NOT NULL,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS postings (
            term TEXT NOT NULL,
            chunk_id TEXT NOT NULL,
            tf INTEGER NOT NULL,
            PRIMARY KEY (term, chunk_id)
        );
        CREATE TABLE IF NOT EXISTS terms (
            term TEXT PRIMARY KEY,
            df INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS stats (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_postings_term ON postings(term);
        CREATE INDEX IF NOT EXISTS idx_chunks_memory_id ON chunks(memory_id);
        """
    )
    version = connection.execute("SELECT value FROM stats WHERE key = 'schema_version'").fetchone()
    if version is not None and version["value"] != _KEYWORD_SCHEMA_VERSION:
        _reset_keyword_schema(connection)
        _ensure_keyword_schema(connection)
        return
    connection.execute(
        "INSERT OR REPLACE INTO stats(key, value) VALUES ('schema_version', ?)",
        (_KEYWORD_SCHEMA_VERSION,),
    )


def _sync_keyword_stats(connection: sqlite3.Connection) -> dict:
    connection.execute("DELETE FROM terms")
    connection.execute(
        """
        INSERT INTO terms(term, df)
        SELECT term, COUNT(*) AS df
        FROM postings
        GROUP BY term
        """
    )
    total_chunks = int(connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()["count"])
    avg_chunk_len_row = connection.execute("SELECT AVG(chunk_len) AS avg_len FROM chunks").fetchone()
    avg_chunk_len = float(avg_chunk_len_row["avg_len"] or 0.0)
    connection.execute("INSERT OR REPLACE INTO stats(key, value) VALUES ('total_chunks', ?)", (str(total_chunks),))
    connection.execute("INSERT OR REPLACE INTO stats(key, value) VALUES ('avg_chunk_len', ?)", (str(avg_chunk_len),))
    connection.execute(
        "INSERT OR REPLACE INTO stats(key, value) VALUES ('schema_version', ?)",
        (_KEYWORD_SCHEMA_VERSION,),
    )
    return {"total_chunks": total_chunks, "avg_chunk_len": avg_chunk_len}


def _delete_keyword_memory_rows(connection: sqlite3.Connection, memory_id: str) -> None:
    connection.execute(
        "DELETE FROM postings WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE memory_id = ?)",
        (memory_id,),
    )
    connection.execute("DELETE FROM chunks WHERE memory_id = ?", (memory_id,))


def _keyword_stats(connection: sqlite3.Connection) -> dict:
    rows = connection.execute("SELECT key, value FROM stats").fetchall()
    stats = {row["key"]: row["value"] for row in rows}
    return {
        "total_chunks": int(float(stats.get("total_chunks", "0"))),
        "avg_chunk_len": float(stats.get("avg_chunk_len", "0") or 0.0),
        "schema_version": stats.get("schema_version"),
    }


def _index_keyword_memory_document(
    config_path: str,
    memory_id: str,
    memory_type: str,
    conversation_id: str,
    title: str,
    relative_path: str,
    content: str,
) -> dict:
    settings = _keyword_settings(config_path)
    if not settings["enabled"]:
        return {"enabled": False, "status": "skipped"}
    if settings["backend"] != "sqlite_bm25":
        return {
            "enabled": True,
            "backend": settings["backend"],
            "status": "error",
            "error": {"type": "ValueError", "message": "keyword_memory.backend must be sqlite_bm25"},
        }
    try:
        with _keyword_sqlite_connect(config_path) as connection:
            _ensure_keyword_schema(connection)
            _delete_keyword_memory_rows(connection, memory_id)
            stopwords = _load_stopwords(config_path)
            rows = []
            postings = []
            chunks = _chunk_text(content, settings["chunk_size"], settings["chunk_overlap"])
            for chunk_index, chunk in enumerate(chunks):
                term_counts = Counter(_keyword_terms(chunk, stopwords, config_path))
                if not term_counts:
                    continue
                chunk_id = f"{memory_id}::keyword_chunk_{chunk_index}"
                chunk_len = sum(term_counts.values())
                rows.append(
                    (
                        chunk_id,
                        memory_id,
                        memory_type,
                        conversation_id or "",
                        title,
                        relative_path,
                        chunk[:8192],
                        chunk_index,
                        chunk_len,
                        now_iso(),
                    )
                )
                postings.extend((term, chunk_id, count) for term, count in term_counts.items())
            if rows:
                connection.executemany(
                    """
                    INSERT OR REPLACE INTO chunks(
                        chunk_id, memory_id, memory_type, conversation_id, title, path,
                        content, chunk_index, chunk_len, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
            if postings:
                connection.executemany(
                    "INSERT OR REPLACE INTO postings(term, chunk_id, tf) VALUES (?, ?, ?)",
                    postings,
                )
            stats = _sync_keyword_stats(connection)
            connection.commit()
        return {
            "enabled": True,
            "backend": "sqlite_bm25",
            "index_type": "sqlite_inverted_index",
            "status": "success",
            "chunk_count": len(rows),
            "term_count": len(postings),
            "total_chunks": stats["total_chunks"],
            "index_path": str(_keyword_sqlite_path(config_path)),
        }
    except Exception as exc:
        return {
            "enabled": True,
            "backend": "sqlite_bm25",
            "index_type": "sqlite_inverted_index",
            "status": "error",
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }


def _rebuild_keyword_index(config_path: str, paths: dict[str, Path | int], index: dict) -> dict:
    with _keyword_sqlite_connect(config_path, paths) as connection:
        _ensure_keyword_schema(connection)
        _reset_keyword_schema(connection)
        _ensure_keyword_schema(connection)
        connection.commit()
    indexed = 0
    errors = []
    for memory_id in sorted(index):
        document, error = _memory_document_from_index(paths, index, memory_id)
        if error:
            errors.append(error)
            continue
        metadata = document["metadata"]
        result = _index_keyword_memory_document(
            config_path,
            memory_id,
            metadata.get("memory_type", ""),
            metadata.get("conversation_id", ""),
            metadata.get("title", memory_id),
            document["path"],
            document["content"],
        )
        if result.get("status") == "success":
            indexed += result.get("chunk_count", 0)
        else:
            errors.append(result.get("error", {"type": "KeywordIndexError", "message": str(result)}))
    return {
        "enabled": True,
        "backend": "sqlite_bm25",
        "index_type": "sqlite_inverted_index",
        "status": "rebuilt" if not errors else "partial",
        "row_count": indexed,
        "errors": errors,
        "index_path": str(_keyword_sqlite_path(config_path, paths)),
    }


def _ensure_keyword_index(config_path: str, paths: dict[str, Path | int], index: dict) -> dict:
    settings = _keyword_settings(config_path)
    if not settings["enabled"]:
        return {"enabled": False, "status": "skipped"}
    if settings["backend"] != "sqlite_bm25":
        raise ValueError("keyword_memory.backend must be sqlite_bm25")
    db_path = _keyword_sqlite_path(config_path, paths)
    if not db_path.exists():
        return _rebuild_keyword_index(config_path, paths, index)
    with _keyword_sqlite_connect(config_path, paths) as connection:
        _ensure_keyword_schema(connection)
        stats = _keyword_stats(connection)
        row_count = stats["total_chunks"]
        connection.commit()
    if row_count <= 0:
        return _rebuild_keyword_index(config_path, paths, index)
    return {
        "enabled": True,
        "backend": "sqlite_bm25",
        "index_type": "sqlite_inverted_index",
        "status": "ready",
        "row_count": row_count,
        "index_path": str(db_path),
    }


def _bm25_score(tf: int, df: int, total_chunks: int, chunk_len: int, avg_chunk_len: float, k1: float, b: float) -> float:
    if tf <= 0 or df <= 0 or total_chunks <= 0:
        return 0.0
    avg_len = avg_chunk_len if avg_chunk_len > 0 else 1.0
    idf = math.log(1.0 + (total_chunks - df + 0.5) / (df + 0.5))
    denominator = tf + k1 * (1.0 - b + b * (chunk_len / avg_len))
    return idf * ((tf * (k1 + 1.0)) / denominator)


def _keyword_bm25_hits(
    config_path: str,
    paths: dict[str, Path | int],
    query_terms: list[str],
) -> list[dict]:
    settings = _keyword_settings(config_path)
    term_counts = Counter(query_terms)
    with _keyword_sqlite_connect(config_path, paths) as connection:
        stats = _keyword_stats(connection)
        if stats["total_chunks"] <= 0:
            return []
        scores = {}
        matched_terms: dict[str, set[str]] = {}
        chunk_rows = {}
        for term, query_tf in term_counts.items():
            rows = connection.execute(
                """
                SELECT
                    p.tf,
                    t.df,
                    c.chunk_id,
                    c.memory_id,
                    c.memory_type,
                    c.conversation_id,
                    c.title,
                    c.path,
                    c.content,
                    c.chunk_index,
                    c.chunk_len
                FROM postings p
                JOIN terms t ON t.term = p.term
                JOIN chunks c ON c.chunk_id = p.chunk_id
                WHERE p.term = ?
                """,
                (term,),
            ).fetchall()
            for row in rows:
                score = _bm25_score(
                    int(row["tf"]),
                    int(row["df"]),
                    stats["total_chunks"],
                    int(row["chunk_len"]),
                    stats["avg_chunk_len"],
                    settings["bm25_k1"],
                    settings["bm25_b"],
                )
                chunk_id = row["chunk_id"]
                scores[chunk_id] = scores.get(chunk_id, 0.0) + score * query_tf
                matched_terms.setdefault(chunk_id, set()).add(term)
                chunk_rows[chunk_id] = row
        hits = []
        for chunk_id, score in sorted(scores.items(), key=lambda item: item[1], reverse=True):
            row = chunk_rows[chunk_id]
            hits.append(
                {
                    "score": score,
                    "matched_terms": sorted(matched_terms.get(chunk_id, set())),
                    "entity": {
                        "chunk_id": row["chunk_id"],
                        "memory_id": row["memory_id"],
                        "memory_type": row["memory_type"],
                        "conversation_id": row["conversation_id"],
                        "title": row["title"],
                        "path": row["path"],
                        "content": row["content"],
                        "chunk_index": row["chunk_index"],
                    },
                }
            )
    return hits


def _candidate_key(candidate: dict, dedupe_by_memory_id: bool) -> str:
    memory_id = candidate.get("memory_id")
    if dedupe_by_memory_id and memory_id:
        return f"memory::{memory_id}"
    return f"chunk::{candidate.get('chunk_id') or memory_id or id(candidate)}"


def _dedupe_source_candidates(candidates: list[dict]) -> list[dict]:
    """Keep only the highest-ranked chunk for each memory within one retrieval source."""
    deduped = []
    seen = set()
    for candidate in candidates:
        key = candidate.get("memory_id") or candidate.get("chunk_id") or id(candidate)
        if key in seen:
            continue
        seen.add(key)
        deduped.append({**candidate, "rank": len(deduped) + 1})
    return deduped


def _title_match_bonus(title: str | None, query_terms: list[str], boost: float) -> float:
    if not title or not query_terms or boost <= 0:
        return 0.0
    lowered = title.lower()
    return boost if any(term and term.lower() in lowered for term in query_terms) else 0.0


def _keyword_memory_candidates(
    config_path: str,
    paths: dict[str, Path | int],
    index: dict,
    query: str,
    existing_memory_ids: set[str] | None = None,
) -> tuple[list[dict], list[dict], list[str]]:
    settings = _keyword_settings(config_path)
    if not settings["enabled"] or not query:
        return [], [], []
    existing_memory_ids = existing_memory_ids or set()
    query_terms = _keyword_terms(query, _load_stopwords(config_path), config_path)
    if not query_terms:
        return [], [], []

    try:
        ready = _ensure_keyword_index(config_path, paths, index)
        if int(ready.get("row_count", 0)) == 0:
            return [], [], query_terms
        hits = _keyword_bm25_hits(config_path, paths, query_terms)
    except Exception as exc:
        return (
            [],
            [{"type": type(exc).__name__, "message": str(exc), "source": "keyword_memory"}],
            query_terms,
        )

    candidates = []
    for rank, hit in enumerate(hits, start=1):
        if len(candidates) >= settings["candidate_k"]:
            break
        metadata = hit.get("entity", {}) or {}
        memory_id = metadata.get("memory_id")
        if memory_id in existing_memory_ids:
            continue
        text = metadata.get("content", "")
        candidates.append(
            {
                "source": "keyword",
                "rank": rank,
                "chunk_id": metadata.get("chunk_id"),
                "memory_id": memory_id,
                "memory_type": metadata.get("memory_type"),
                "conversation_id": metadata.get("conversation_id"),
                "title": metadata.get("title", memory_id or "Keyword Memory Chunk"),
                "path": metadata.get("path", ""),
                "content": text,
                "original_chars": len(text),
                "chunk_index": metadata.get("chunk_index"),
                "source_scores": {"keyword": float(hit.get("score", 0.0))},
                "matched_terms": hit.get("matched_terms", []),
                "query_terms": query_terms[:20],
            }
        )
    return candidates, [], query_terms


def _finalize_retrieval_candidates(
    candidates: list[dict],
    remaining_chars: int,
    final_top_k: int,
    retrieval_type: str,
    query_terms: list[str],
) -> tuple[list[dict], bool]:
    docs = []
    used = 0
    any_truncated = False
    for candidate in candidates:
        if len(docs) >= final_top_k or used >= remaining_chars:
            break
        text = candidate.get("content", "")
        included = text[: remaining_chars - used]
        used += len(included)
        truncated = len(included) < len(text)
        any_truncated = any_truncated or truncated
        retrieval = {
            "type": retrieval_type,
            "chunk_id": candidate.get("chunk_id"),
            "chunk_index": candidate.get("chunk_index"),
            "query_terms": query_terms[:20],
        }
        retrieval.update(candidate.get("retrieval", {}))
        docs.append(
            {
                "memory_id": candidate.get("memory_id") or f"{retrieval_type}_chunk_{len(docs)}",
                "memory_type": candidate.get("memory_type"),
                "conversation_id": candidate.get("conversation_id"),
                "title": candidate.get("title"),
                "path": candidate.get("path", ""),
                "content": included,
                "original_chars": len(text),
                "included_chars": len(included),
                "truncated": truncated,
                "retrieval": retrieval,
            }
        )
    return docs, any_truncated


def _search_keyword_memory(
    config_path: str,
    paths: dict[str, Path | int],
    index: dict,
    query: str,
    remaining_chars: int,
    existing_memory_ids: set[str] | None = None,
) -> tuple[list[dict], list[dict], bool]:
    settings = _keyword_settings(config_path)
    if not settings["enabled"] or not query or remaining_chars <= 0:
        return [], [], False
    candidates, errors, query_terms = _keyword_memory_candidates(
        config_path,
        paths,
        index,
        query,
        existing_memory_ids,
    )
    for candidate in candidates:
        candidate["retrieval"] = {
            "index_type": "sqlite_inverted_index",
            "score": round(float(candidate.get("source_scores", {}).get("keyword", 0.0)), 6),
            "matched_terms": candidate.get("matched_terms", []),
        }
    docs, any_truncated = _finalize_retrieval_candidates(
        candidates,
        remaining_chars,
        settings["top_k"],
        "sqlite_bm25",
        query_terms,
    )
    return docs, errors, any_truncated


def _chunk_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    # Vector retrieval works on chunks; overlap reduces the chance of cutting important context.
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size")
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = end - chunk_overlap
    return chunks


def _get_embedding_model(config_path: str | Path):
    settings = _vector_settings(config_path)
    config_file = Path(config_path).resolve()
    model_path = resolve_from_file(settings["embedding_model_path"], config_file)
    cache_key = str(model_path)
    if cache_key in _EMBEDDING_MODEL_CACHE:
        return _EMBEDDING_MODEL_CACHE[cache_key]
    from transformers import AutoModel, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(str(model_path), local_files_only=True)
    model = AutoModel.from_pretrained(str(model_path), local_files_only=True)
    model.eval()
    _EMBEDDING_MODEL_CACHE[cache_key] = (tokenizer, model)
    return tokenizer, model


def _embed_texts(config_path: str | Path, texts: list[str], is_query: bool = False) -> list[list[float]]:
    if not texts:
        return []
    import torch
    import torch.nn.functional as functional

    settings = _vector_settings(config_path)
    tokenizer, model = _get_embedding_model(config_path)
    prepared = texts
    if is_query and settings["query_instruction"]:
        prepared = [settings["query_instruction"] + text for text in texts]
    with torch.no_grad():
        encoded = tokenizer(prepared, padding=True, truncation=True, max_length=512, return_tensors="pt")
        output = model(**encoded)
        token_embeddings = output.last_hidden_state
        mask = encoded["attention_mask"].unsqueeze(-1).expand(token_embeddings.size()).float()
        pooled = (token_embeddings * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-9)
        normalized = functional.normalize(pooled, p=2, dim=1)
    return normalized.cpu().tolist()


def _get_milvus_client(config_path: str | Path):
    from pymilvus import DataType, MilvusClient

    config_file = Path(config_path).resolve()
    settings = _vector_settings(config_file)
    if settings["backend"] != "milvus":
        raise ValueError("vector_memory.backend must be milvus")
    client = _milvus_client_for_db(config_file, settings["db_path"])
    collection_name = settings["collection_name"]
    if not client.has_collection(collection_name):
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(field_name="chunk_id", datatype=DataType.VARCHAR, is_primary=True, max_length=256)
        schema.add_field(field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=settings["embedding_dim"])
        schema.add_field(field_name="memory_id", datatype=DataType.VARCHAR, max_length=128)
        schema.add_field(field_name="memory_type", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="title", datatype=DataType.VARCHAR, max_length=512)
        schema.add_field(field_name="path", datatype=DataType.VARCHAR, max_length=512)
        schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=8192)
        schema.add_field(field_name="chunk_index", datatype=DataType.INT64)
        index_params = client.prepare_index_params()
        index_params.add_index(field_name="embedding", index_type="AUTOINDEX", metric_type="COSINE")
        client.create_collection(collection_name=collection_name, schema=schema, index_params=index_params)
    return client


def _index_memory_document(
    config_path: str,
    memory_id: str,
    memory_type: str,
    title: str,
    relative_path: str,
    content: str,
) -> dict:
    # Save chunks to Milvus after writing the Markdown memory. Failure is recorded, not fatal.
    settings = _vector_settings(config_path)
    if not settings["enabled"]:
        return {"enabled": False, "status": "skipped"}
    try:
        client = _get_milvus_client(config_path)
        chunks = _chunk_text(content, settings["chunk_size"], settings["chunk_overlap"])
        embeddings = _embed_texts(config_path, chunks, is_query=False)
        rows = []
        for index, chunk in enumerate(chunks):
            rows.append(
                {
                    "chunk_id": f"{memory_id}::chunk_{index}",
                    "embedding": embeddings[index],
                    "memory_id": memory_id,
                    "memory_type": memory_type,
                    "title": title,
                    "path": relative_path,
                    "content": chunk[:8192],
                    "chunk_index": index,
                }
            )
        collection_name = settings["collection_name"]
        escaped_memory_id = memory_id.replace("\\", "\\\\").replace('"', '\\"')
        client.delete(collection_name=collection_name, filter=f'memory_id == "{escaped_memory_id}"')
        if rows:
            client.insert(collection_name=collection_name, data=rows)
        return {"enabled": True, "backend": "milvus", "status": "success", "chunk_count": len(rows)}
    except Exception as exc:
        return {
            "enabled": True,
            "backend": "milvus",
            "status": "error",
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }


def _ensure_vector_index(config_path: str, paths: dict[str, Path | int], index: dict) -> dict:
    settings = _vector_settings(config_path)
    if not settings["enabled"]:
        return {"enabled": False, "status": "skipped"}
    client = _get_milvus_client(config_path)
    stats = client.get_collection_stats(settings["collection_name"])
    row_count = int(stats.get("row_count", 0))
    if row_count > 0:
        return {"enabled": True, "backend": "milvus", "status": "ready", "row_count": row_count}
    indexed = 0
    for memory_id in sorted(index):
        document, error = _memory_document_from_index(paths, index, memory_id)
        if error:
            continue
        metadata = document["metadata"]
        result = _index_memory_document(
            config_path,
            memory_id,
            metadata.get("memory_type", ""),
            metadata.get("title", memory_id),
            document["path"],
            document["content"],
        )
        if result.get("status") == "success":
            indexed += result.get("chunk_count", 0)
    return {"enabled": True, "backend": "milvus", "status": "rebuilt", "row_count": indexed}


def _vector_distance_to_similarity(distance: object, metric_type: str) -> float:
    try:
        value = float(distance)
    except (TypeError, ValueError):
        return 0.0
    metric = metric_type.upper()
    if metric in {"COSINE", "IP"}:
        return value
    if metric == "L2":
        return 1.0 / (1.0 + max(value, 0.0))
    return value


def _normalize_vector_similarity(similarity: float, metric_type: str) -> float:
    metric = metric_type.upper()
    if metric == "COSINE":
        return max(0.0, min((similarity + 1.0) / 2.0, 1.0))
    if metric == "IP":
        return max(0.0, min(similarity, 1.0))
    if metric == "L2":
        return max(0.0, min(similarity, 1.0))
    return max(0.0, min(similarity, 1.0))


def _vector_memory_candidates(
    config_path: str,
    paths: dict[str, Path | int],
    index: dict,
    query: str,
    existing_memory_ids: set[str] | None = None,
) -> tuple[list[dict], list[dict], list[str]]:
    settings = _vector_settings(config_path)
    metric_type = "COSINE"
    if not settings["enabled"] or not query:
        return [], [], []
    existing_memory_ids = existing_memory_ids or set()
    query_terms = _keyword_terms(query, _load_stopwords(config_path), config_path)
    try:
        ready = _ensure_vector_index(config_path, paths, index)
        if int(ready.get("row_count", 0)) == 0:
            return [], [], query_terms
        client = _get_milvus_client(config_path)
        _load_milvus_collection(client, settings["collection_name"])
        query_embedding = _embed_texts(config_path, [query], is_query=True)
        result = client.search(
            collection_name=settings["collection_name"],
            data=query_embedding,
            anns_field="embedding",
            limit=settings["candidate_k"],
            search_params={"metric_type": metric_type},
            output_fields=["chunk_id", "memory_id", "memory_type", "title", "path", "content", "chunk_index"],
        )
    except Exception as exc:
        return (
            [],
            [{"type": type(exc).__name__, "message": str(exc), "source": "vector_memory"}],
            query_terms,
        )

    hits = result[0] if result else []
    candidates = []
    for hit in hits:
        metadata = hit.get("entity", {}) or {}
        memory_id = metadata.get("memory_id")
        if memory_id in existing_memory_ids:
            continue
        text = metadata.get("content", "")
        raw_distance = hit.get("distance")
        vector_similarity = _vector_distance_to_similarity(raw_distance, metric_type)
        normalized_vector_score = _normalize_vector_similarity(vector_similarity, metric_type)
        title_boost = _title_match_bonus(metadata.get("title"), query_terms, settings["title_match_boost"])
        candidates.append(
            {
                "source": "vector",
                "rank": len(candidates) + 1,
                "chunk_id": metadata.get("chunk_id") or hit.get("id"),
                "memory_id": memory_id,
                "memory_type": metadata.get("memory_type", "vector"),
                "conversation_id": metadata.get("conversation_id"),
                "title": metadata.get("title", memory_id or "Vector Memory Chunk"),
                "path": metadata.get("path", ""),
                "content": text,
                "original_chars": len(text),
                "chunk_index": metadata.get("chunk_index"),
                "source_scores": {
                    "vector_raw_distance": raw_distance if raw_distance is not None else 0.0,
                    "vector_similarity": vector_similarity,
                    "vector_normalized": normalized_vector_score,
                    "vector_rerank": normalized_vector_score + title_boost,
                },
                "matched_terms": [],
                "query_terms": query_terms[:20],
            }
        )
    candidates.sort(key=lambda item: item["source_scores"]["vector_rerank"], reverse=True)
    for rank, candidate in enumerate(candidates, start=1):
        candidate["rank"] = rank
    return candidates[: settings["candidate_k"]], [], query_terms


def _search_vector_memory(
    config_path: str,
    paths: dict[str, Path | int],
    index: dict,
    query: str,
    remaining_chars: int,
    existing_memory_ids: set[str] | None = None,
) -> tuple[list[dict], list[dict], bool]:
    settings = _vector_settings(config_path)
    if not settings["enabled"] or not query or remaining_chars <= 0:
        return [], [], False
    candidates, errors, query_terms = _vector_memory_candidates(
        config_path,
        paths,
        index,
        query,
        existing_memory_ids,
    )
    for candidate in candidates:
        source_scores = candidate.get("source_scores", {})
        candidate["retrieval"] = {
            "raw_distance": source_scores.get("vector_raw_distance"),
            "similarity": round(float(source_scores.get("vector_similarity", 0.0)), 6),
            "normalized_score": round(float(source_scores.get("vector_normalized", 0.0)), 6),
            "rerank_score": round(float(source_scores.get("vector_rerank", 0.0)), 6),
        }
    docs, any_truncated = _finalize_retrieval_candidates(
        candidates,
        remaining_chars,
        settings["top_k"],
        "milvus_vector",
        query_terms,
    )
    return docs, errors, any_truncated


def _fuse_retrieval_candidates(
    keyword_candidates: list[dict],
    vector_candidates: list[dict],
    query_terms: list[str],
    config_path: str,
) -> list[dict]:
    settings = _fusion_settings(config_path)
    if settings["dedupe_by_memory_id"]:
        keyword_candidates = _dedupe_source_candidates(keyword_candidates)
        vector_candidates = _dedupe_source_candidates(vector_candidates)
    if not settings["enabled"]:
        combined = keyword_candidates + vector_candidates
        combined.sort(key=lambda item: (item.get("rank", 10**9), item.get("source", "")))
        return combined[: settings["final_top_k"]]
    if settings["strategy"] != "rrf":
        raise ValueError("retrieval_fusion.strategy must be rrf")

    merged = {}
    source_lists = [
        ("keyword", keyword_candidates, settings["keyword_weight"]),
        ("vector", vector_candidates, settings["vector_weight"]),
    ]
    for source, candidates, weight in source_lists:
        for rank, candidate in enumerate(candidates, start=1):
            key = _candidate_key(candidate, settings["dedupe_by_memory_id"])
            contribution = weight / (settings["rrf_k"] + rank)
            current = merged.get(key)
            if current is None:
                current = {
                    **candidate,
                    "source_scores": {},
                    "source_ranks": {},
                    "matched_terms": set(),
                    "_fusion_score": 0.0,
                    "_best_contribution": -1.0,
                }
                merged[key] = current
            if contribution > current["_best_contribution"]:
                for field in (
                    "chunk_id",
                    "memory_id",
                    "memory_type",
                    "conversation_id",
                    "title",
                    "path",
                    "content",
                    "original_chars",
                    "chunk_index",
                ):
                    current[field] = candidate.get(field)
                current["_best_contribution"] = contribution
            current["_fusion_score"] += contribution
            current["source_ranks"][source] = min(rank, current["source_ranks"].get(source, rank))
            for score_name, score in candidate.get("source_scores", {}).items():
                current["source_scores"][score_name] = score
            current["matched_terms"].update(candidate.get("matched_terms", []))

    fused = []
    for candidate in merged.values():
        title_boost = _title_match_bonus(candidate.get("title"), query_terms, settings["title_match_boost"])
        fusion_score = candidate["_fusion_score"] + title_boost
        source_scores = {
            key: round(float(value), 6)
            for key, value in sorted(candidate.get("source_scores", {}).items())
        }
        source_ranks = {
            key: int(value)
            for key, value in sorted(candidate.get("source_ranks", {}).items())
        }
        candidate["retrieval"] = {
            "strategy": "rrf",
            "fusion_score": round(float(fusion_score), 6),
            "source_scores": source_scores,
            "source_ranks": source_ranks,
            "sources": sorted(source_ranks),
            "matched_terms": sorted(candidate.get("matched_terms", set())),
        }
        candidate["_fusion_score"] = fusion_score
        fused.append(candidate)
    fused.sort(key=lambda item: item["_fusion_score"], reverse=True)
    return fused[: settings["final_top_k"]]


def _read_index(index_path: Path) -> dict:
    # memory_index.json 是 memory_id 到 Markdown 文档路径和元信息的索引。
    if not index_path.exists():
        return {}
    index = read_json(index_path)
    if not isinstance(index, dict):
        raise ValueError("memory_index.json must be an object")
    return index


def load_memory(
    config_path: str,
    selected_memory_ids: list[str],
    use_global_memory: bool,
    query: str | None = None,
    outdir: str | None = None,
) -> dict:
    # Load explicit/global memory first, then fill the remaining budget with keyword and vector search results.
    if not isinstance(selected_memory_ids, list) or not all(isinstance(item, str) for item in selected_memory_ids):
        raise ValueError("selected_memory_ids must be a list of strings")
    paths = _memory_paths(config_path)
    index = _read_index(paths["index"])
    ordered_ids = []
    if use_global_memory:
        # 全局记忆会自动加入；它通常保存项目背景、固定规则或长期知识。
        ordered_ids.extend(sorted(key for key, item in index.items() if item.get("memory_type") == "global"))
    # selected_memory_ids 来自 runtime_input.json，是用户或调用方显式指定的记忆。
    ordered_ids.extend(selected_memory_ids)
    ordered_ids = list(dict.fromkeys(ordered_ids))

    docs = []
    errors = []
    remaining = int(paths["max_chars"])
    any_truncated = False
    for memory_id in ordered_ids:
        document, error = _memory_document_from_index(paths, index, memory_id)
        if error:
            errors.append(error)
            continue
        metadata = document["metadata"]
        relative_path = document["path"]
        original = document["content"]
        included = original[:remaining] if remaining > 0 else ""
        truncated = len(included) < len(original)
        any_truncated = any_truncated or truncated
        if included:
            docs.append(
                {
                    "memory_id": memory_id,
                    "memory_type": metadata.get("memory_type"),
                    "title": metadata.get("title", memory_id),
                    "path": relative_path,
                    "content": included,
                    "original_chars": len(original),
                    "included_chars": len(included),
                    "truncated": truncated,
                }
            )
            remaining -= len(included)
    keyword_docs = []
    vector_docs = []
    fused_docs = []
    search_text = query or ""
    if search_text and remaining > 0:
        existing_ids = {item["memory_id"] for item in docs}
        keyword_candidates, keyword_errors, keyword_terms = _keyword_memory_candidates(
            config_path,
            paths,
            index,
            search_text,
            existing_ids,
        )
        vector_candidates, vector_errors, vector_terms = _vector_memory_candidates(
            config_path,
            paths,
            index,
            search_text,
            existing_ids,
        )
        errors.extend(keyword_errors)
        errors.extend(vector_errors)
        query_terms = []
        seen_terms = set()
        for term in keyword_terms + vector_terms:
            if term in seen_terms:
                continue
            seen_terms.add(term)
            query_terms.append(term)
        fused_candidates = _fuse_retrieval_candidates(keyword_candidates, vector_candidates, query_terms, config_path)
        fused_docs, fused_truncated = _finalize_retrieval_candidates(
            fused_candidates,
            remaining,
            _fusion_settings(config_path)["final_top_k"],
            "hybrid_rrf",
            query_terms,
        )
        docs.extend(fused_docs)
        any_truncated = any_truncated or fused_truncated
        for item in fused_docs:
            sources = set(item.get("retrieval", {}).get("sources", []))
            if "keyword" in sources:
                keyword_docs.append(item)
            if "vector" in sources:
                vector_docs.append(item)
    if errors and docs:
        status = "partial"
    elif errors:
        status = "error"
    else:
        status = "success"
    result = {
        "status": status,
        "query": query,
        "selected_memory_docs": docs,
        "max_memory_chars": paths["max_chars"],
        "total_chars": sum(item["included_chars"] for item in docs),
        "truncated": any_truncated,
        "errors": errors,
        "keyword_memory": {
            "enabled": _keyword_settings(config_path)["enabled"],
            "retrieved_count": len(keyword_docs),
        },
        "vector_memory": {
            "enabled": _vector_settings(config_path)["enabled"],
            "retrieved_count": len(vector_docs),
        },
    }
    if outdir:
        output_dir = Path(outdir)
        write_json(result, output_dir / "selected_memory.json")
        append_jsonl(
            {
                "timestamp": now_iso(),
                "operation": "load",
                "status": status,
                "selected_ids": [item["memory_id"] for item in docs],
                "keyword_retrieved_count": len(keyword_docs),
                "vector_retrieved_count": len(vector_docs),
                "errors": errors,
            },
            output_dir / "memory_log.jsonl",
        )
    return result


def _split_memory_statements(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    parts = re.split(r"(?<=[\u3002\uff01\uff1f\uff1b])\s*|(?<=[.!?;])\s+|\n+", normalized)
    statements = []
    for part in parts:
        statement = part.strip(" -\t\r\n")
        if statement:
            statements.append(statement)
    return statements or [normalized]


def _strip_memory_role_prefix(statement: str) -> str:
    return re.sub(r"^(user|assistant|system)\s*[:：]\s*", "", statement.strip(), flags=re.IGNORECASE)


def _normalize_memory_statement(statement: str) -> str:
    cleaned = _strip_memory_role_prefix(statement)
    return re.sub(r"\s+", "", cleaned).lower().strip("\u3002\uff01\uff1f\uff1b!?;,.，、")


def _statement_terms(statement: str, config_path: str) -> set[str]:
    return set(_keyword_terms(_strip_memory_role_prefix(statement), _load_stopwords(config_path), config_path))


def _statement_bm25_index(statements: list[str], config_path: str) -> dict:
    stopwords = _load_stopwords(config_path)
    docs = []
    doc_freq = Counter()
    for index, statement in enumerate(statements):
        terms = _keyword_terms(_strip_memory_role_prefix(statement), stopwords, config_path)
        term_counts = Counter(terms)
        docs.append(
            {
                "index": index,
                "statement": statement,
                "term_counts": term_counts,
                "length": sum(term_counts.values()),
            }
        )
        for term in term_counts:
            doc_freq[term] += 1
    total_docs = len(docs)
    avg_doc_len = sum(doc["length"] for doc in docs) / total_docs if total_docs else 0.0
    return {
        "docs": docs,
        "doc_freq": doc_freq,
        "total_docs": total_docs,
        "avg_doc_len": avg_doc_len,
    }


def _best_statement_bm25_match(statements: list[str], query_statement: str, config_path: str) -> dict:
    bm25_index = _statement_bm25_index(statements, config_path)
    if bm25_index["total_docs"] == 0:
        return {"index": None, "similarity": 0.0, "score": 0.0, "term_coverage": 0.0, "matched_terms": []}

    stopwords = _load_stopwords(config_path)
    query_terms = _keyword_terms(_strip_memory_role_prefix(query_statement), stopwords, config_path)
    query_counts = Counter(query_terms)
    if not query_counts:
        return {"index": None, "similarity": 0.0, "score": 0.0, "term_coverage": 0.0, "matched_terms": []}

    settings = _keyword_settings(config_path)
    total_docs = bm25_index["total_docs"]
    avg_doc_len = bm25_index["avg_doc_len"]
    doc_freq = bm25_index["doc_freq"]
    best = {"index": None, "similarity": 0.0, "score": 0.0, "matched_terms": []}
    for doc in bm25_index["docs"]:
        score = 0.0
        matched_terms = []
        for term, query_tf in query_counts.items():
            tf = doc["term_counts"].get(term, 0)
            df = doc_freq.get(term, 0)
            if tf <= 0 or df <= 0:
                continue
            matched_terms.append(term)
            score += query_tf * _bm25_score(
                tf,
                df,
                total_docs,
                doc["length"],
                avg_doc_len,
                settings["bm25_k1"],
                settings["bm25_b"],
            )
        if score > best["score"]:
            best = {
                "index": doc["index"],
                "similarity": 0.0,
                "score": score,
                "term_coverage": 0.0,
                "matched_terms": sorted(matched_terms),
            }

    query_len = sum(query_counts.values())
    max_score = 0.0
    for term, query_tf in query_counts.items():
        df = doc_freq.get(term, 0)
        if df <= 0:
            continue
        max_score += query_tf * _bm25_score(
            query_tf,
            df,
            total_docs,
            query_len,
            avg_doc_len,
            settings["bm25_k1"],
            settings["bm25_b"],
        )
    if max_score > 0:
        matched_tf = sum(query_counts[term] for term in best["matched_terms"])
        term_coverage = matched_tf / max(query_len, 1)
        best["term_coverage"] = term_coverage
        best["similarity"] = min(best["score"] / max_score, 1.0) * term_coverage
    return best


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def _best_statement_vector_match(statements: list[str], query_statement: str, config_path: str) -> dict:
    if not statements or not query_statement.strip():
        return {"index": None, "similarity": 0.0, "error": None}
    try:
        prepared = [_strip_memory_role_prefix(statement) for statement in statements]
        query = _strip_memory_role_prefix(query_statement)
        embeddings = _embed_texts(config_path, prepared + [query], is_query=False)
    except Exception as exc:
        return {
            "index": None,
            "similarity": 0.0,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
    if len(embeddings) != len(statements) + 1:
        return {"index": None, "similarity": 0.0, "error": None}
    query_embedding = embeddings[-1]
    best_index = None
    best_score = 0.0
    for index, embedding in enumerate(embeddings[:-1]):
        score = _cosine_similarity(embedding, query_embedding)
        if score > best_score:
            best_index = index
            best_score = score
    return {"index": best_index, "similarity": best_score, "error": None}


def _merge_memory_content(old_content: str, new_content: str, config_path: str, conflict_strategy: str = "mark") -> dict:
    if conflict_strategy not in {"mark", "prefer_new", "prefer_old"}:
        raise ValueError("conflict_strategy must be mark, prefer_new, or prefer_old")
    old_statements = _split_memory_statements(old_content)
    new_statements = _split_memory_statements(new_content)
    merged = list(old_statements)
    normalized_to_index = {
        _normalize_memory_statement(statement): index
        for index, statement in enumerate(merged)
    }
    duplicates = []
    supplements = []
    conflicts = []
    semantic_match_errors = []

    for new_statement in new_statements:
        normalized_new = _normalize_memory_statement(new_statement)
        if normalized_new in normalized_to_index:
            duplicates.append({"new": new_statement, "old": merged[normalized_to_index[normalized_new]]})
            continue

        best_match = _best_statement_bm25_match(merged, new_statement, config_path)
        best_index = best_match["index"]
        best_score = best_match["similarity"]
        match_method = "bm25"
        vector_match = None
        if best_index is None or best_score < 0.72:
            vector_match = _best_statement_vector_match(merged, new_statement, config_path)
            if vector_match.get("error"):
                semantic_match_errors.append(vector_match["error"])
            elif vector_match["index"] is not None and vector_match["similarity"] >= 0.82:
                best_index = vector_match["index"]
                best_score = vector_match["similarity"]
                match_method = "vector"

        if best_index is not None and best_score >= 0.72:
            old_statement = merged[best_index]
            conflict = {
                "old": old_statement,
                "new": new_statement,
                "match_method": match_method,
                "resolution": conflict_strategy,
            }
            if match_method == "bm25":
                conflict.update(
                    {
                        "bm25_similarity": round(best_score, 4),
                        "bm25_score": round(float(best_match["score"]), 6),
                        "term_coverage": round(float(best_match["term_coverage"]), 4),
                        "matched_terms": best_match["matched_terms"],
                    }
                )
            else:
                conflict.update(
                    {
                        "semantic_similarity": round(best_score, 4),
                        "bm25_similarity": round(float(best_match["similarity"]), 4),
                        "bm25_score": round(float(best_match["score"]), 6),
                        "matched_terms": best_match["matched_terms"],
                    }
                )
            conflicts.append(conflict)
            if conflict_strategy == "prefer_new":
                merged[best_index] = new_statement
            elif conflict_strategy == "mark":
                merged.append(f"[CONFLICT] old: {old_statement} | new: {new_statement}")
            continue

        supplements.append({"new": new_statement})
        merged.append(new_statement)
        normalized_to_index[normalized_new] = len(merged) - 1

    return {
        "merged_content": "\n".join(merged).strip(),
        "old_statement_count": len(old_statements),
        "new_statement_count": len(new_statements),
        "duplicate_count": len(duplicates),
        "supplement_count": len(supplements),
        "conflict_count": len(conflicts),
        "duplicates": duplicates,
        "supplements": supplements,
        "conflicts": conflicts,
        "semantic_match_errors": semantic_match_errors,
        "conflict_strategy": conflict_strategy,
    }


def _memory_text_from_update_payload(payload: dict, base: Path) -> str:
    for key in ("content", "new_content"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("content_path", "new_content_path"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return read_text((base / value).resolve()).strip()

    messages_path = payload.get("messages_path")
    answer_path = payload.get("answer_path")
    parts = []
    if isinstance(messages_path, str) and messages_path.strip():
        messages = read_json((base / messages_path).resolve())
        if not isinstance(messages, list):
            raise ValueError("messages_path must point to a JSON array")
        message_text = _messages_to_memory_text(messages)
        if message_text:
            parts.append(message_text)
    if isinstance(answer_path, str) and answer_path.strip():
        answer = read_text((base / answer_path).resolve()).strip()
        if answer:
            parts.append(answer)
    if parts:
        return "\n\n".join(parts)
    raise ValueError("update input must provide content, content_path, messages_path, or answer_path")


def update_memory(
    config_path: str,
    memory_id: str,
    update_input_path: str,
    outdir: str | None = None,
) -> dict:
    paths = _memory_paths(config_path)
    index = _read_index(paths["index"])
    document, error = _memory_document_from_index(paths, index, memory_id)
    if error:
        raise ValueError(error["message"])
    payload_path = Path(update_input_path)
    payload = read_json(payload_path)
    if not isinstance(payload, dict):
        raise ValueError("update input must be a JSON object")
    payload_memory_id = payload.get("memory_id")
    if payload_memory_id is not None and payload_memory_id != memory_id:
        raise ValueError("update input memory_id must match --update_memory_id")

    new_content = _memory_text_from_update_payload(payload, payload_path.parent)
    conflict_strategy = payload.get("conflict_strategy", "mark")
    merge_result = _merge_memory_content(document["content"], new_content, config_path, conflict_strategy)
    merged_content = merge_result["merged_content"]
    summary_result = _summarize_memory_content(merged_content, config_path)
    now = now_iso()
    metadata = document["metadata"]
    title = metadata.get("title", memory_id)
    memory_type = metadata.get("memory_type", "conversation")
    conversation_id = metadata.get("conversation_id")
    relative_path = document["path"]
    target_path = paths["root"] / relative_path
    summary = summary_result["summary"][:200]
    keywords = ", ".join(item["word"] for item in summary_result["keywords"])
    summary_meta = (
        f"- summarized: `{str(summary_result['summarized']).lower()}`\n"
        f"- source_chars: `{summary_result['source_chars']}`\n"
        f"- summary_chars: `{summary_result['summary_chars']}`\n"
    )
    if keywords:
        summary_meta += f"- keywords: `{keywords}`\n"

    markdown = (
        f"# {title}\n\n"
        f"- memory_id: `{memory_id}`\n"
        f"- memory_type: `{memory_type}`\n"
        f"- conversation_id: `{conversation_id}`\n"
        f"- created_or_updated_at: `{now}`\n\n"
        "## Memory Summary\n\n"
        f"{summary_result['summary']}\n\n"
        "## Summary Metadata\n\n"
        f"{summary_meta}\n"
        "## Merged Memory\n\n"
        f"{merged_content}\n\n"
        "## Update Merge Report\n\n```json\n"
        f"{json.dumps({key: value for key, value in merge_result.items() if key != 'merged_content'}, ensure_ascii=False, indent=2)}\n"
        "```\n\n"
        "## Previous Content\n\n```text\n"
        f"{document['content']}\n```\n\n"
        "## New Content\n\n```text\n"
        f"{new_content}\n```\n"
    )
    write_text(markdown, target_path)

    created_at = metadata.get("created_at", now)
    index[memory_id] = {
        **metadata,
        "memory_id": memory_id,
        "memory_type": memory_type,
        "title": title,
        "summary": summary,
        "path": relative_path,
        "conversation_id": conversation_id,
        "created_at": created_at,
        "updated_at": now,
        "summary_source_chars": summary_result["source_chars"],
        "summary_chars": summary_result["summary_chars"],
        "summarized": summary_result["summarized"],
        "last_update_conflicts": merge_result["conflict_count"],
        "last_update_duplicates": merge_result["duplicate_count"],
        "last_update_supplements": merge_result["supplement_count"],
    }
    write_json(index, paths["index"])
    keyword_index_result = _index_keyword_memory_document(
        config_path,
        memory_id,
        memory_type,
        conversation_id or "",
        title,
        relative_path,
        merged_content,
    )
    vector_index = _index_memory_document(
        config_path,
        memory_id,
        memory_type,
        title,
        relative_path,
        merged_content,
    )
    result = {
        "status": "success",
        "operation": "update",
        "memory_id": memory_id,
        "memory_type": memory_type,
        "title": title,
        "path": relative_path,
        "updated_at": now,
        "merge_result": merge_result,
        "summary_info": summary_result,
        "keyword_index": keyword_index_result,
        "vector_index": vector_index,
    }
    if outdir:
        output_dir = Path(outdir)
        write_json(result, output_dir / "updated_memory.json")
        append_jsonl(
            {
                "timestamp": now,
                "operation": "update",
                "status": "success",
                "memory_id": memory_id,
                "duplicate_count": merge_result["duplicate_count"],
                "supplement_count": merge_result["supplement_count"],
                "conflict_count": merge_result["conflict_count"],
                "keyword_index": keyword_index_result,
                "vector_index": vector_index,
            },
            output_dir / "memory_log.jsonl",
        )
    return result


def _safe_conversation_id(conversation_id: str) -> str:
    # conversation_id 会成为文件名的一部分，必须限制字符，避免路径逃逸。
    if not isinstance(conversation_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", conversation_id):
        raise ValueError("conversation_id may only contain letters, numbers, dot, underscore, and hyphen")
    return conversation_id


def save_memory(
    config_path: str,
    conversation_id: str,
    save_type: str,
    messages_path: str,
    trace_path: str,
    answer_path: str,
    outdir: str | None = None,
) -> dict:
    # B1 在任务结束后调用该函数：把 messages、trace 和最终回答沉淀为新的 memory 文档。
    conversation_id = _safe_conversation_id(conversation_id)
    if save_type not in {"conversation", "global"}:
        raise ValueError("save_type must be conversation or global")
    paths = _memory_paths(config_path)
    messages = read_json(messages_path)
    trace = read_json(trace_path)
    answer = read_text(answer_path).strip()
    if not isinstance(messages, list) or not isinstance(trace, dict):
        raise ValueError("messages must be an array and trace must be an object")
    now = now_iso()
    memory_id = f"mem_{save_type}_{conversation_id}"
    target_dir = paths["conversations"] if save_type == "conversation" else paths["global"]
    relative_dir = "conversations" if save_type == "conversation" else "global"
    target_path = Path(target_dir) / f"{conversation_id}.md"
    relative_path = f"{relative_dir}/{conversation_id}.md"
    title = f"{save_type.title()} {conversation_id}"
    memory_source_text = _messages_to_memory_text(messages) or answer
    summary_result = _summarize_memory_content(memory_source_text, config_path)
    summary = summary_result["summary"][:200]
    keywords = ", ".join(item["word"] for item in summary_result["keywords"])
    summary_meta = (
        f"- summarized: `{str(summary_result['summarized']).lower()}`\n"
        f"- source_chars: `{summary_result['source_chars']}`\n"
        f"- summary_chars: `{summary_result['summary_chars']}`\n"
    )
    if keywords:
        summary_meta += f"- keywords: `{keywords}`\n"
    markdown = (
        f"# {title}\n\n"
        f"- memory_id: `{memory_id}`\n"
        f"- conversation_id: `{conversation_id}`\n"
        f"- created_or_updated_at: `{now}`\n\n"
        "## Memory Summary\n\n"
        f"{summary_result['summary']}\n\n"
        "## Summary Metadata\n\n"
        f"{summary_meta}\n"
        "## Final Answer\n\n"
        f"{answer}\n\n"
        "## Messages\n\n```json\n"
        f"{json.dumps(messages, ensure_ascii=False, indent=2)}\n```\n\n"
        "## Trace\n\n```json\n"
        f"{json.dumps(trace, ensure_ascii=False, indent=2)}\n```\n"
    )
    # 正式保存 Markdown 记忆文档。
    write_text(markdown, target_path)
    # 更新 memory_index.json，使后续可以通过 memory_id 重新找到该文档。
    index = _read_index(paths["index"])
    existing = index.get(memory_id, {})
    created_at = existing.get("created_at", now)
    index[memory_id] = {
        "memory_id": memory_id,
        "memory_type": save_type,
        "title": title,
        "summary": summary,
        "path": relative_path,
        "conversation_id": conversation_id,
        "created_at": created_at,
        "updated_at": now,
        "summary_source_chars": summary_result["source_chars"],
        "summary_chars": summary_result["summary_chars"],
        "summarized": summary_result["summarized"],
    }
    write_json(index, paths["index"])
    keyword_index_result = _index_keyword_memory_document(
        config_path,
        memory_id,
        save_type,
        conversation_id,
        title,
        relative_path,
        _memory_content_for_use({"memory_type": save_type}, markdown),
    )
    vector_index = _index_memory_document(
        config_path,
        memory_id,
        save_type,
        title,
        relative_path,
        _memory_content_for_use({"memory_type": save_type}, markdown),
    )
    result = {
        "status": "success",
        "memory_id": memory_id,
        "memory_type": save_type,
        "conversation_id": conversation_id,
        "title": title,
        "summary": summary,
        "path": relative_path,
        "index_path": Path(paths["index"]).name,
        "created_at": created_at,
        "updated_at": now,
        "source_paths": {
            "messages": str(messages_path),
            "trace": str(trace_path),
            "answer": str(answer_path),
        },
        "summary_info": summary_result,
        "keyword_index": keyword_index_result,
        "vector_index": vector_index,
    }
    if outdir:
        output_dir = Path(outdir)
        write_json(result, output_dir / "saved_memory.json")
        append_jsonl(
            {
                "timestamp": now,
                "operation": "save",
                "status": "success",
                "memory_id": memory_id,
                "keyword_index": keyword_index_result,
                "vector_index": vector_index,
            },
            output_dir / "memory_log.jsonl",
        )
    return result


def parse_bool(value: str) -> bool:
    # argparse 传入的是字符串，这里把 true/false 等写法转成真正的 bool。
    lowered = value.lower()
    if lowered in {"true", "1", "yes"}:
        return True
    if lowered in {"false", "0", "no"}:
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def build_parser() -> argparse.ArgumentParser:
    # 让 B5 能独立命令行运行：既可以查找 memory，也可以保存 memory。
    parser = argparse.ArgumentParser(description="Select or save local memory documents.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--select_memory_ids", nargs="*")
    parser.add_argument("--use_global_memory", type=parse_bool)
    parser.add_argument("--query")
    parser.add_argument("--save_type", choices=["conversation", "global"])
    parser.add_argument("--save_input_path")
    parser.add_argument("--update_memory_id")
    parser.add_argument("--update_input_path")
    parser.add_argument("--outdir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    # CLI 入口：有 save_type/save_input_path 就保存记忆，否则进入查找记忆模式。
    args = build_parser().parse_args(argv)
    try:
        config_path = resolve_cli_path(args.config)
        outdir = resolve_cli_path(args.outdir)
        if args.update_memory_id or args.update_input_path:
            if not args.update_memory_id or not args.update_input_path:
                raise ValueError("--update_memory_id and --update_input_path must be provided together")
            input_path = resolve_cli_path(args.update_input_path)
            result = update_memory(
                str(config_path),
                args.update_memory_id,
                str(input_path),
                str(outdir),
            )
            print(outdir / "updated_memory.json")
        elif args.save_type or args.save_input_path:
            if not args.save_type or not args.save_input_path:
                raise ValueError("--save_type and --save_input_path must be provided together")
            input_path = resolve_cli_path(args.save_input_path)
            payload = read_json(input_path)
            if payload.get("save_type") != args.save_type:
                raise ValueError("CLI save_type must match memory_save_input.json")
            base = input_path.parent
            result = save_memory(
                str(config_path),
                payload["conversation_id"],
                args.save_type,
                str((base / payload["messages_path"]).resolve()),
                str((base / payload["trace_path"]).resolve()),
                str((base / payload["answer_path"]).resolve()),
                str(outdir),
            )
            print(outdir / "saved_memory.json")
        else:
            if args.select_memory_ids is None and args.use_global_memory is None:
                raise ValueError("select mode requires --select_memory_ids or --use_global_memory")
            result = load_memory(
                str(config_path),
                args.select_memory_ids or [],
                bool(args.use_global_memory),
                args.query,
                str(outdir),
            )
            print(outdir / "selected_memory.json")
        return 0
    except Exception as exc:
        print(f"fatal: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
