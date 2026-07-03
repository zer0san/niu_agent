from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

from common.io_utils import append_jsonl, read_json, read_text, read_yaml, write_json, write_text
from common.logging_utils import now_iso
from common.path_utils import resolve_cli_path, resolve_from_file

_EMBEDDING_MODEL_CACHE = {}
_STOPWORDS_CACHE = {}


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
        "chunk_size": int(vector.get("chunk_size", 500)),
        "chunk_overlap": int(vector.get("chunk_overlap", 80)),
    }


def _keyword_settings(config_path: str | Path) -> dict:
    path = Path(config_path).resolve()
    config = read_yaml(path)
    keyword = config.get("keyword_memory", {}) if isinstance(config, dict) else {}
    if not isinstance(keyword, dict):
        keyword = {}
    return {
        "enabled": bool(keyword.get("enabled", False)),
        "backend": keyword.get("backend", "milvus"),
        "db_path": keyword.get("db_path", "../memory/milvus_memory.db"),
        "collection_name": keyword.get("collection_name", "memory_keyword_chunks"),
        "stopwords_path": keyword.get("stopwords_path", "../memory/stopwords_baidu.txt"),
        "top_k": int(keyword.get("top_k", 3)),
        "chunk_size": int(keyword.get("chunk_size", 500)),
        "chunk_overlap": int(keyword.get("chunk_overlap", 80)),
        "inverted_index_algo": keyword.get("inverted_index_algo", "DAAT_MAXSCORE"),
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


def _keyword_terms(text: str, stopwords: set[str] | None = None) -> list[str]:
    if not text:
        return []
    stopwords = stopwords or set()
    terms = []
    for match in re.finditer(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", text.lower()):
        token = match.group(0)
        if re.fullmatch(r"[A-Za-z0-9_]+", token):
            if len(token) > 1 and token not in stopwords:
                terms.append(token)
            continue
        for size in (2, 3, 4):
            for index in range(0, max(len(token) - size + 1, 0)):
                gram = token[index : index + size]
                if gram not in stopwords:
                    terms.append(gram)
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


def _keyword_sparse_vector(text: str, config_path: str | Path) -> dict[int, float]:
    stopwords = _load_stopwords(config_path)
    term_counts = Counter(_keyword_terms(text, stopwords))
    if not term_counts:
        return {}
    norm = sum(count * count for count in term_counts.values()) ** 0.5 or 1.0
    sparse = {}
    for term, count in term_counts.items():
        digest = hashlib.blake2b(term.encode("utf-8"), digest_size=8).digest()
        term_id = int.from_bytes(digest, "big") % 2147483647
        sparse[term_id] = sparse.get(term_id, 0.0) + count / norm
    return sparse


def _get_keyword_milvus_client(config_path: str | Path):
    from pymilvus import DataType, MilvusClient

    settings = _keyword_settings(config_path)
    if settings["backend"] != "milvus":
        raise ValueError("keyword_memory.backend must be milvus")
    client = _milvus_client_for_db(config_path, settings["db_path"])
    collection_name = settings["collection_name"]
    if not client.has_collection(collection_name):
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(field_name="chunk_id", datatype=DataType.VARCHAR, is_primary=True, max_length=256)
        schema.add_field(field_name="sparse", datatype=DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field(field_name="memory_id", datatype=DataType.VARCHAR, max_length=128)
        schema.add_field(field_name="memory_type", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="conversation_id", datatype=DataType.VARCHAR, max_length=128)
        schema.add_field(field_name="title", datatype=DataType.VARCHAR, max_length=512)
        schema.add_field(field_name="path", datatype=DataType.VARCHAR, max_length=512)
        schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=8192)
        schema.add_field(field_name="chunk_index", datatype=DataType.INT64)
        index_params = client.prepare_index_params()
        index_params.add_index(
            field_name="sparse",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="IP",
            params={"inverted_index_algo": settings["inverted_index_algo"]},
        )
        client.create_collection(collection_name=collection_name, schema=schema, index_params=index_params)
    return client


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
    try:
        client = _get_keyword_milvus_client(config_path)
        rows = []
        chunks = _chunk_text(content, settings["chunk_size"], settings["chunk_overlap"])
        for chunk_index, chunk in enumerate(chunks):
            sparse = _keyword_sparse_vector(chunk, config_path)
            if not sparse:
                continue
            rows.append(
                {
                    "chunk_id": f"{memory_id}::keyword_chunk_{chunk_index}",
                    "sparse": sparse,
                    "memory_id": memory_id,
                    "memory_type": memory_type,
                    "conversation_id": conversation_id or "",
                    "title": title,
                    "path": relative_path,
                    "content": chunk[:8192],
                    "chunk_index": chunk_index,
                }
            )
        collection_name = settings["collection_name"]
        escaped_memory_id = memory_id.replace("\\", "\\\\").replace('"', '\\"')
        client.delete(collection_name=collection_name, filter=f'memory_id == "{escaped_memory_id}"')
        if rows:
            client.insert(collection_name=collection_name, data=rows)
        return {
            "enabled": True,
            "backend": "milvus",
            "index_type": "SPARSE_INVERTED_INDEX",
            "status": "success",
            "chunk_count": len(rows),
            "collection_name": collection_name,
        }
    except Exception as exc:
        return {
            "enabled": True,
            "backend": "milvus",
            "index_type": "SPARSE_INVERTED_INDEX",
            "status": "error",
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }


def _ensure_keyword_index(config_path: str, paths: dict[str, Path | int], index: dict) -> dict:
    settings = _keyword_settings(config_path)
    if not settings["enabled"]:
        return {"enabled": False, "status": "skipped"}
    indexed = 0
    for memory_id in sorted(index):
        document, error = _memory_document_from_index(paths, index, memory_id)
        if error:
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
    return {
        "enabled": True,
        "backend": "milvus",
        "index_type": "SPARSE_INVERTED_INDEX",
        "status": "synced",
        "row_count": indexed,
    }


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
    existing_memory_ids = existing_memory_ids or set()
    query_sparse = _keyword_sparse_vector(query, config_path)
    if not query_sparse:
        return [], [], False

    try:
        ready = _ensure_keyword_index(config_path, paths, index)
        if int(ready.get("row_count", 0)) == 0:
            return [], [], False
        client = _get_keyword_milvus_client(config_path)
        _load_milvus_collection(client, settings["collection_name"])
        result = client.search(
            collection_name=settings["collection_name"],
            data=[query_sparse],
            anns_field="sparse",
            limit=settings["top_k"] + len(existing_memory_ids),
            search_params={"metric_type": "IP", "params": {}},
            output_fields=[
                "chunk_id",
                "memory_id",
                "memory_type",
                "conversation_id",
                "title",
                "path",
                "content",
                "chunk_index",
            ],
        )
    except Exception as exc:
        return (
            [],
            [{"type": type(exc).__name__, "message": str(exc), "source": "keyword_memory"}],
            False,
        )

    docs = []
    used = 0
    any_truncated = False
    hits = result[0] if result else []
    query_terms = _keyword_terms(query, _load_stopwords(config_path))
    for hit in hits:
        metadata = hit.get("entity", {}) or {}
        memory_id = metadata.get("memory_id")
        if memory_id in existing_memory_ids:
            continue
        if len(docs) >= settings["top_k"]:
            break
        if used >= remaining_chars:
            break
        text = metadata.get("content", "")
        included = text[: remaining_chars - used]
        used += len(included)
        truncated = len(included) < len(text)
        any_truncated = any_truncated or truncated
        docs.append(
            {
                "memory_id": memory_id or f"keyword_chunk_{len(docs)}",
                "memory_type": metadata.get("memory_type"),
                "conversation_id": metadata.get("conversation_id"),
                "title": metadata.get("title", memory_id or "Keyword Memory Chunk"),
                "path": metadata.get("path", ""),
                "content": included,
                "original_chars": len(text),
                "included_chars": len(included),
                "truncated": truncated,
                "retrieval": {
                    "type": "milvus_sparse_inverted",
                    "index_type": "SPARSE_INVERTED_INDEX",
                    "chunk_id": metadata.get("chunk_id") or hit.get("id"),
                    "chunk_index": metadata.get("chunk_index"),
                    "score": hit.get("distance"),
                    "query_terms": query_terms[:20],
                },
            }
        )
    return docs, [], any_truncated


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


def _search_vector_memory(
    config_path: str,
    paths: dict[str, Path | int],
    index: dict,
    query: str,
    remaining_chars: int,
    existing_memory_ids: set[str] | None = None,
) -> tuple[list[dict], list[dict], bool]:
    # Search related memory chunks in Milvus and return the same shape as selected_memory_docs.
    settings = _vector_settings(config_path)
    if not settings["enabled"] or not query or remaining_chars <= 0:
        return [], [], False
    existing_memory_ids = existing_memory_ids or set()
    try:
        ready = _ensure_vector_index(config_path, paths, index)
        if int(ready.get("row_count", 0)) == 0:
            return [], [], False
        client = _get_milvus_client(config_path)
        _load_milvus_collection(client, settings["collection_name"])
        query_embedding = _embed_texts(config_path, [query], is_query=True)
        result = client.search(
            collection_name=settings["collection_name"],
            data=query_embedding,
            anns_field="embedding",
            limit=settings["top_k"],
            search_params={"metric_type": "COSINE"},
            output_fields=["memory_id", "memory_type", "title", "path", "content", "chunk_index"],
        )
    except Exception as exc:
        return (
            [],
            [{"type": type(exc).__name__, "message": str(exc), "source": "vector_memory"}],
            False,
        )

    docs = []
    used = 0
    any_truncated = False
    hits = result[0] if result else []
    for hit in hits:
        metadata = hit.get("entity", {}) or {}
        memory_id = metadata.get("memory_id")
        if memory_id in existing_memory_ids:
            continue
        if used >= remaining_chars:
            break
        text = metadata.get("content", "")
        included = text[: remaining_chars - used]
        used += len(included)
        truncated = len(included) < len(text)
        any_truncated = any_truncated or truncated
        docs.append(
            {
                "memory_id": memory_id or f"vector_chunk_{len(docs)}",
                "memory_type": metadata.get("memory_type", "vector"),
                "title": metadata.get("title", memory_id or "Vector Memory Chunk"),
                "path": metadata.get("path", ""),
                "content": included,
                "original_chars": len(text),
                "included_chars": len(included),
                "truncated": truncated,
                "retrieval": {
                    "type": "milvus_vector",
                    "chunk_index": metadata.get("chunk_index"),
                    "score": hit.get("distance"),
                },
            }
        )
    return docs, [], any_truncated


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
    keyword_docs, keyword_errors, keyword_truncated = _search_keyword_memory(
        config_path,
        paths,
        index,
        query or "",
        remaining,
        {item["memory_id"] for item in docs},
    )
    docs.extend(keyword_docs)
    errors.extend(keyword_errors)
    any_truncated = any_truncated or keyword_truncated
    remaining -= sum(item["included_chars"] for item in keyword_docs)
    vector_docs, vector_errors, vector_truncated = _search_vector_memory(
        config_path,
        paths,
        index,
        query or "",
        remaining,
        {item["memory_id"] for item in docs},
    )
    docs.extend(vector_docs)
    errors.extend(vector_errors)
    any_truncated = any_truncated or vector_truncated
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
    return set(_keyword_terms(_strip_memory_role_prefix(statement), _load_stopwords(config_path)))


def _statement_overlap(left: str, right: str, config_path: str) -> float:
    left_terms = _statement_terms(left, config_path)
    right_terms = _statement_terms(right, config_path)
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms & right_terms) / max(1, min(len(left_terms), len(right_terms)))


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

    for new_statement in new_statements:
        normalized_new = _normalize_memory_statement(new_statement)
        if normalized_new in normalized_to_index:
            duplicates.append({"new": new_statement, "old": merged[normalized_to_index[normalized_new]]})
            continue

        best_index = None
        best_score = 0.0
        for index, old_statement in enumerate(merged):
            score = _statement_overlap(old_statement, new_statement, config_path)
            if score > best_score:
                best_index = index
                best_score = score

        if best_index is not None and best_score >= 0.72:
            old_statement = merged[best_index]
            conflict = {
                "old": old_statement,
                "new": new_statement,
                "overlap": round(best_score, 4),
                "resolution": conflict_strategy,
            }
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
