"""
通用 RAG (Retrieval-Augmented Generation) 框架

提供向量索引、BM25 索引、Embedding 模型等通用功能，
不包含具体业务逻辑。
"""
from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import Any
import re

logger = logging.getLogger("rag")

_DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_EMBEDDINGS = None


# ============================================================
# 文本处理
# ============================================================

def tokenize(text: str) -> list[str]:
    """
    分词：支持中文单字 + 英文/数字单词
    
    Example:
        tokenize("红烧肉 recipe") -> ["红", "烧", "肉", "recipe"]
    """
    if not text:
        return []
    return re.findall(r"[\u4e00-\u9fff]|[a-zA-Z0-9]+", text.lower())


def keyword_score(query: str, text: str) -> float:
    """
    计算查询与文本的关键词匹配得分
    
    Returns: 0.0 ~ 1.0，表示查询词被文本覆盖的比例
    """
    q_tokens = set(tokenize(query))
    if not q_tokens:
        return 0.0
    t_tokens = set(tokenize(text))
    if not t_tokens:
        return 0.0
    return float(len(q_tokens & t_tokens)) / float(len(q_tokens))


# ============================================================
# FAISS 向量索引
# ============================================================

def build_faiss_index(texts: list[str], model_name: str | None = None):
    """
    构建 FAISS 向量索引
    
    Args:
        texts: 待索引的文本列表
        model_name: Embedding 模型名称
    
    Returns:
        FAISS IndexFlatIP 索引
    """
    import numpy as np  # type: ignore
    import faiss  # type: ignore

    embeddings_model = get_embedding_model(model_name)
    vectors = embeddings_model.embed_documents(texts)
    embeddings = np.asarray(vectors, dtype="float32")
    index = faiss.IndexFlatIP(embeddings.shape[1])
    index.add(embeddings)
    return index


def save_faiss_index(index, path: Path) -> None:
    """保存 FAISS 索引到文件"""
    import faiss  # type: ignore

    path.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(path))
    logger.info("FAISS index saved to %s", path)


def load_faiss_index(path: Path):
    """加载 FAISS 索引"""
    import faiss  # type: ignore

    if not path.exists():
        logger.warning("FAISS index not found: %s", path)
        return None
    return faiss.read_index(str(path))


# ============================================================
# BM25 关键词索引
# ============================================================

def build_bm25_index(texts: list[str]):
    """
    构建 BM25 关键词索引
    
    Args:
        texts: 待索引的文本列表
    
    Returns:
        BM25Okapi 索引
    """
    from rank_bm25 import BM25Okapi
    tokenized_corpus = [tokenize(text) for text in texts]
    return BM25Okapi(tokenized_corpus)


def save_bm25_index(bm25, path: Path) -> None:
    """保存 BM25 索引到文件"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(bm25, f)
    logger.info("BM25 index saved to %s", path)


def load_bm25_index(path: Path):
    """加载 BM25 索引"""
    if not path.exists():
        logger.warning("BM25 index not found: %s", path)
        return None
    try:
        with path.open("rb") as f:
            return pickle.load(f)
    except Exception as e:
        logger.error("Failed to load BM25 index: %s", e)
        return None


# ============================================================
# Embedding 模型
# ============================================================

def get_embedding_model(model_name: str | None = None):
    """
    获取 HuggingFace Embedding 模型（单例模式）
    
    Args:
        model_name: 模型名称，默认使用多语言 MiniLM
    
    Returns:
        HuggingFaceEmbeddings 实例
    """
    global _EMBEDDINGS
    if _EMBEDDINGS is not None:
        return _EMBEDDINGS
    
    import warnings
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        from langchain_community.embeddings import HuggingFaceEmbeddings
        _EMBEDDINGS = HuggingFaceEmbeddings(
            model_name=model_name or _DEFAULT_MODEL,
            encode_kwargs={"normalize_embeddings": True},
        )
    return _EMBEDDINGS


def warmup(model_name: str | None = None) -> None:
    """
    预加载 Embedding 模型，避免首次请求延迟
    
    应在应用启动时调用
    """
    import warnings
    logger.info("🚀 RAG warmup started...")
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            warnings.filterwarnings("ignore", category=FutureWarning)
            model = get_embedding_model(model_name)
            model.embed_query("warmup")
        logger.info("✅ RAG warmup completed - embedding model ready")
    except Exception as e:
        logger.warning("⚠️ RAG warmup skipped: %s", e)


# ============================================================
# 向量搜索
# ============================================================

def vector_search(
    query: str,
    index,
    metadata: list[dict[str, Any]],
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """
    向量相似度搜索
    
    Args:
        query: 搜索查询
        index: FAISS 索引
        metadata: 元数据列表（与索引顺序对应）
        top_k: 返回结果数量
    
    Returns:
        [{"index": int, "score": float, "meta": dict}, ...]
    """
    import numpy as np
    
    if index is None:
        return []
    
    embeddings_model = get_embedding_model()
    embedding = embeddings_model.embed_query(query)
    embedding = np.asarray([embedding], dtype="float32")
    scores, indices = index.search(embedding, top_k)
    
    results = []
    for idx, score in zip(indices[0], scores[0]):
        if idx < 0 or idx >= len(metadata):
            continue
        results.append({
            "index": idx,
            "score": float(score),
            "meta": metadata[idx],
        })
    return results


def bm25_search(
    query: str,
    bm25,
    metadata: list[dict[str, Any]],
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """
    BM25 关键词搜索
    
    Args:
        query: 搜索查询
        bm25: BM25 索引
        metadata: 元数据列表
        top_k: 返回结果数量
    
    Returns:
        [{"index": int, "score": float, "meta": dict}, ...]
    """
    if bm25 is None:
        return []
    
    tokenized_query = tokenize(query)
    if not tokenized_query:
        return []
    
    scores = bm25.get_scores(tokenized_query)
    top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
    
    results = []
    for idx in top_indices:
        if scores[idx] <= 0:
            continue
        meta = metadata[idx] if idx < len(metadata) else {}
        # Normalize BM25 score to ~0-1 range
        normalized_score = min(scores[idx] / 10.0, 1.0)
        results.append({
            "index": idx,
            "score": float(normalized_score),
            "meta": meta,
        })
    return results
