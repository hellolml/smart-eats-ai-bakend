"""
食谱 RAG 搜索工具

基于向量 + BM25 混合检索的食谱搜索。
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent.rag import base as rag
from app.agent.tools.native import RuntimeContext
from app.domain.recipe import recipe_index

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
    
    index_path = recipe_index.default_index_path()
    meta_path = recipe_index.default_meta_path()
    bm25_path = recipe_index.default_bm25_path()
    
    # Load FAISS index
    _INDEX = rag.load_faiss_index(index_path)
    if _INDEX is None:
        error = f"FAISS index not found: {index_path}"
        logger.warning(error)
        return False, error
    
    # Load metadata
    _META = recipe_index.load_metadata(meta_path)
    if not _META:
        error = f"Metadata not found or empty: {meta_path}"
        logger.warning(error)
        return False, error
    
    # Load BM25 index (optional)
    _BM25 = rag.load_bm25_index(bm25_path)
    if _BM25 is None:
        logger.info("BM25 index not available, using simple keyword search")
    
    logger.info("RAG indices loaded: %d recipes", len(_META))
    return True, None


def _expand_query(query: str) -> str:
    """Expand query with synonyms for better recall."""
    expanded_terms = [query]
    query_lower = query.lower()
    
    for keyword, synonyms in _SYNONYMS.items():
        if keyword in query_lower:
            expanded_terms.extend(synonyms[:3])
    
    expanded = " ".join(expanded_terms)
    if expanded != query:
        logger.debug("Query expanded: '%s' -> '%s'", query, expanded)
    return expanded


class RagSearchRecipesArgs(BaseModel):
    query: str = Field(..., description="Recipe search query.")
    top_k: int | None = Field(default=None, description="Maximum number of results.")
    min_score: float | None = Field(default=None, description="Minimum score threshold.")
    filters: dict[str, Any] | None = Field(default=None, description="Optional metadata filters.")
    runtime_context: RuntimeContext = Field(default_factory=dict)


async def _rag_search_recipes(
    query: str,
    top_k: int | None = None,
    min_score: float | None = None,
    filters: dict[str, Any] | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = runtime_context or {}
    query = str(query or "").strip()
    
    # Fallback: use last user message if query is empty
    if not query:
        query = str(ctx.get("last_user_message") or "").strip()
        if query:
            logger.info("rag_search_recipes: using last_user_message as query fallback: %s", query)
    
    if not query:
        return {"items": [], "error": "Empty query"}
    
    top_k = top_k or 5
    try:
        top_k = int(top_k)
    except (TypeError, ValueError):
        top_k = 5
    
    min_score = min_score or 0.0
    try:
        min_score = float(min_score)
    except (TypeError, ValueError):
        min_score = 0.0
    
    filters = filters if isinstance(filters, dict) else {}

    # Load indices
    success, error = _load_index()
    if not success:
        return {"items": [], "error": error}

    # Expand query with synonyms
    expanded_query = _expand_query(query)
    
    # Hybrid search using generic RAG functions
    vector_hits: list[dict[str, Any]] = []
    if rag.has_embedding_support():
        vector_hits = rag.vector_search(expanded_query, _INDEX, _META, top_k=top_k)
    keyword_hits = _keyword_search(expanded_query, top_k=top_k)
    merged = _merge_hits(vector_hits, keyword_hits, top_k=top_k)
    
    return {"items": _format_items(merged, filters, min_score)}


rag_search_recipes_tool = StructuredTool.from_function(
    coroutine=_rag_search_recipes,
    name="rag_search_recipes",
    description=(
        "Search local recipe knowledge base with keyword + vector recall. "
        "Input: {query:string, top_k?:integer, min_score?:number, filters?:object}."
    ),
    args_schema=RagSearchRecipesArgs,
    infer_schema=False,
)


def _keyword_search(query: str, top_k: int) -> list[dict[str, Any]]:
    """Use BM25 or fallback to simple keyword matching."""
    if _BM25 is not None:
        return rag.bm25_search(query, _BM25, _META, top_k=top_k)
    
    # Fallback: simple token matching
    results = []
    for idx, meta in enumerate(_META or []):
        text = str(meta.get("text") or "")
        score = rag.keyword_score(query, text)
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
    """Merge vector and keyword search results."""
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
    """Format search results with filtering."""
    items: list[dict[str, Any]] = []
    
    for item in hits:
        score = float(item.get("score") or 0.0)
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
    """Apply tag filters."""
    if not filters:
        return True
    
    tags = item.get("metadata", {}).get("tags") or []
    if isinstance(filters.get("tags"), list):
        required = set(filters.get("tags") or [])
        if required and not required.issubset(set(tags)):
            return False
    
    return True
