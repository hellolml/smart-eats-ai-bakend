from __future__ import annotations

import logging
from typing import Any

from app.agent.tools_registry import register_tool
from app.agent.rag import recipes_index

logger = logging.getLogger("rag")

_INDEX = None
_META: list[dict[str, Any]] | None = None
_BM25 = None

# 同义词扩展表（中文美食领域）
_SYNONYMS: dict[str, list[str]] = {
    "辣": ["麻辣", "川菜", "辣椒", "香辣", "微辣", "重辣"],
    "甜": ["甜点", "甜品", "糖", "蜜", "甜味"],
    "酸": ["酸甜", "醋", "柠檬", "酸辣"],
    "清淡": ["低盐", "少油", "健康", "清蒸", "白灼"],
    "快手": ["简单", "快速", "10分钟", "懒人", "速成"],
    "减肥": ["低脂", "低卡", "健身", "轻食", "沙拉"],
    "早餐": ["早点", "早饭", "晨间"],
    "夜宵": ["宵夜", "深夜", "夜食"],
    "素": ["素食", "素菜", "蔬菜", "纯素"],
    "肉": ["荤菜", "肉类", "红肉", "白肉"],
    "汤": ["炖汤", "煲汤", "汤品", "羹"],
    "面": ["面条", "面食", "挂面", "手工面"],
    "饭": ["米饭", "炒饭", "焖饭", "盖饭"],
    "鸡": ["鸡肉", "鸡腿", "鸡翅", "鸡胸"],
    "牛": ["牛肉", "牛腩", "牛排", "牛腱"],
    "鱼": ["鱼肉", "海鱼", "淡水鱼", "鱼片"],
    "虾": ["虾仁", "大虾", "基围虾", "龙虾"],
}


def _load_index() -> tuple[bool, str | None]:
    """Load indices with error handling. Returns (success, error_message)."""
    global _INDEX, _META, _BM25
    
    if _INDEX is not None and _META is not None:
        return True, None
    
    index_path = recipes_index.default_index_path()
    meta_path = recipes_index.default_meta_path()
    bm25_path = recipes_index.default_bm25_path()
    
    # Load FAISS index
    _INDEX = recipes_index.load_faiss_index(index_path)
    if _INDEX is None:
        error = f"FAISS index not found: {index_path}"
        logger.warning(error)
        return False, error
    
    # Load metadata
    _META = recipes_index.load_metadata(meta_path)
    if not _META:
        error = f"Metadata not found or empty: {meta_path}"
        logger.warning(error)
        return False, error
    
    # Load BM25 index (optional, fall back to simple keyword if missing)
    _BM25 = recipes_index.load_bm25_index(bm25_path)
    if _BM25 is None:
        logger.info("BM25 index not available, falling back to simple keyword search")
    
    logger.info("RAG indices loaded: %d recipes", len(_META))
    return True, None


def _expand_query(query: str) -> str:
    """Expand query with synonyms for better recall."""
    expanded_terms = [query]
    query_lower = query.lower()
    
    for keyword, synonyms in _SYNONYMS.items():
        if keyword in query_lower:
            # Add a subset of synonyms to avoid query explosion
            expanded_terms.extend(synonyms[:3])
    
    expanded = " ".join(expanded_terms)
    if expanded != query:
        logger.debug("Query expanded: '%s' -> '%s'", query, expanded)
    return expanded


@register_tool(
    name="rag_search_recipes",
    description=(
        "Search local recipe knowledge base with keyword + vector recall. "
        "Input: {query:string, top_k?:integer, min_score?:number, filters?:object}. "
        "Output: {items:[{id,title,snippet,source,score,metadata}], error?:string}."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer"},
            "min_score": {"type": "number", "description": "Minimum score threshold (0.0-1.0)"},
            "filters": {"type": "object"},
        },
        "required": ["query"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "snippet": {"type": "string"},
                        "source": {"type": "string"},
                        "score": {"type": "number"},
                        "metadata": {"type": "object"},
                    },
                },
            },
            "error": {"type": "string"},
        },
    },
)
async def rag_search_recipes(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"items": [], "error": "Empty query"}
    
    top_k = args.get("top_k") or 5
    try:
        top_k = int(top_k)
    except (TypeError, ValueError):
        top_k = 5
    
    min_score = args.get("min_score") or 0.0
    try:
        min_score = float(min_score)
    except (TypeError, ValueError):
        min_score = 0.0
    
    filters = args.get("filters") if isinstance(args.get("filters"), dict) else {}

    # Load indices with error handling
    success, error = _load_index()
    if not success:
        return {"items": [], "error": error}

    # Expand query with synonyms
    expanded_query = _expand_query(query)
    
    # Hybrid search
    vector_hits = _vector_search(expanded_query, top_k=top_k)
    keyword_hits = _keyword_search(expanded_query, top_k=top_k)
    merged = _merge_hits(vector_hits, keyword_hits, top_k=top_k)
    
    return {"items": _format_items(merged, filters, min_score)}


def _vector_search(query: str, top_k: int) -> list[dict[str, Any]]:
    try:
        import numpy as np  # type: ignore
    except Exception:
        return []
    embeddings_model = recipes_index.get_embedding_model()
    embedding = embeddings_model.embed_query(query)
    embedding = np.asarray([embedding], dtype="float32")
    scores, indices = _INDEX.search(embedding, top_k)
    results = []
    for idx, score in zip(indices[0], scores[0]):
        if idx < 0 or idx >= len(_META):
            continue
        meta = _META[idx]
        results.append({"index": idx, "score": float(score), "meta": meta})
    return results


def _keyword_search(query: str, top_k: int) -> list[dict[str, Any]]:
    """Use BM25 for efficient keyword search, fallback to simple token matching."""
    if _BM25 is not None:
        # Use BM25 for efficient search
        tokenized_query = recipes_index.tokenize(query)
        if not tokenized_query:
            return []
        
        scores = _BM25.get_scores(tokenized_query)
        # Get top_k indices with highest scores
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        
        results = []
        for idx in top_indices:
            if scores[idx] <= 0:
                continue
            meta = _META[idx] if idx < len(_META) else {}
            # Normalize BM25 score to 0-1 range (approximate)
            normalized_score = min(scores[idx] / 10.0, 1.0)
            results.append({"index": idx, "score": float(normalized_score), "meta": meta})
        return results
    
    # Fallback: simple token matching (old method)
    results = []
    for idx, meta in enumerate(_META or []):
        text = str(meta.get("text") or "")
        score = recipes_index.keyword_score(query, text)
        if score <= 0:
            continue
        results.append({"index": idx, "score": float(score), "meta": meta})
    results.sort(key=lambda item: item["score"], reverse=True)
    return results[:top_k]


def _merge_hits(
    vector_hits: list[dict[str, Any]],
    keyword_hits: list[dict[str, Any]],
    top_k: int,
) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    for item in vector_hits:
        idx = item["index"]
        merged.setdefault(idx, {"index": idx, "meta": item["meta"], "score": 0.0})
        merged[idx]["score"] += item["score"] * 0.7
    for item in keyword_hits:
        idx = item["index"]
        merged.setdefault(idx, {"index": idx, "meta": item["meta"], "score": 0.0})
        merged[idx]["score"] += item["score"] * 0.3
    ranked = sorted(merged.values(), key=lambda item: item["score"], reverse=True)
    return ranked[:top_k]


def _format_items(
    hits: list[dict[str, Any]],
    filters: dict[str, Any],
    min_score: float = 0.0,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in hits:
        score = float(item.get("score") or 0.0)
        
        # Apply min_score filter
        if score < min_score:
            continue
        
        meta = item.get("meta") or {}
        payload = {
            "id": str(meta.get("id") or ""),
            "title": str(meta.get("title") or ""),
            "snippet": str(meta.get("text") or "")[:200],
            "source": "local_recipes",
            "score": score,
            "metadata": meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {},
        }
        if _passes_filters(payload, filters):
            items.append(payload)
    return items


def _passes_filters(item: dict[str, Any], filters: dict[str, Any]) -> bool:
    if not filters:
        return True
    tags = item.get("metadata", {}).get("tags") or []
    if isinstance(filters.get("tags"), list):
        required = set(filters.get("tags") or [])
        if required and not required.issubset(set(tags)):
            return False
    return True
