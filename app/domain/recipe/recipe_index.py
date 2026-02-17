"""
食谱索引模块 - 业务层

负责食谱数据的加载、解析和索引路径管理。
使用通用 RAG 框架 (app.agent.rag.base) 进行索引构建和检索。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from app.agent.rag import base as rag

logger = logging.getLogger("recipe")


def _repo_root() -> Path:
    """获取项目根目录"""
    return Path(__file__).resolve().parents[3]


# ============================================================
# 索引文件路径
# ============================================================

def default_data_path() -> Path:
    """食谱数据源文件"""
    return _repo_root() / "data" / "recipes.jsonl"


def default_index_path() -> Path:
    """FAISS 向量索引文件"""
    return _repo_root() / "data" / "recipes.faiss"


def default_meta_path() -> Path:
    """元数据文件"""
    return _repo_root() / "data" / "recipes_meta.jsonl"


def default_bm25_path() -> Path:
    """BM25 索引文件"""
    return _repo_root() / "data" / "recipes_bm25.pkl"


# ============================================================
# 数据模型
# ============================================================

@dataclass
class RecipeDoc:
    """食谱文档"""
    id: str
    title: str
    text: str  # 用于索引的拼接文本
    metadata: dict[str, Any]


# ============================================================
# 数据加载
# ============================================================

def load_recipes(path: Path) -> list[RecipeDoc]:
    """
    从 JSONL 文件加载食谱数据
    
    Args:
        path: JSONL 文件路径
    
    Returns:
        RecipeDoc 列表
    """
    docs: list[RecipeDoc] = []
    if not path.exists():
        logger.warning("Recipe data file not found: %s", path)
        return docs
    
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            
            title = str(item.get("title") or item.get("name") or "").strip()
            if not title:
                continue
            
            doc_id = str(item.get("id") or item.get("recipe_id") or title)
            text = _build_doc_text(item)
            docs.append(RecipeDoc(
                id=doc_id,
                title=title,
                text=text,
                metadata=item if isinstance(item, dict) else {},
            ))
    
    logger.info("Loaded %d recipes from %s", len(docs), path)
    return docs


def _build_doc_text(item: dict[str, Any]) -> str:
    """
    构建用于索引的文本（拼接标题、标签、食材、描述）
    """
    parts: list[str] = []
    
    # 标题
    title = item.get("title") or item.get("name")
    if title:
        parts.append(str(title))
    
    # 标签
    tags = item.get("tags") or []
    if isinstance(tags, list):
        parts.extend([str(tag) for tag in tags if tag])
    
    # 食材
    ingredients = item.get("ingredients") or item.get("items")
    if isinstance(ingredients, list):
        parts.extend([str(ing) for ing in ingredients if ing])
    
    # 描述
    summary = item.get("summary") or item.get("description")
    if summary:
        parts.append(str(summary))
    
    return " ".join(parts)


# ============================================================
# 元数据保存/加载
# ============================================================

def save_metadata(docs: Iterable[RecipeDoc], path: Path) -> None:
    """保存元数据到 JSONL 文件"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for doc in docs:
            payload = {
                "id": doc.id,
                "title": doc.title,
                "text": doc.text,
                "metadata": doc.metadata,
            }
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    logger.info("Metadata saved to %s", path)


def load_metadata(path: Path) -> list[dict[str, Any]]:
    """加载元数据"""
    records: list[dict[str, Any]] = []
    if not path.exists():
        logger.warning("Metadata file not found: %s", path)
        return records
    
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


# ============================================================
# 索引构建（封装通用 RAG 框架）
# ============================================================

def build_faiss_index(docs: list[RecipeDoc], model_name: str | None = None):
    """构建食谱 FAISS 向量索引"""
    texts = [doc.text for doc in docs]
    return rag.build_faiss_index(texts, model_name)


def build_bm25_index(docs: list[RecipeDoc]):
    """构建食谱 BM25 索引"""
    texts = [doc.text for doc in docs]
    return rag.build_bm25_index(texts)
