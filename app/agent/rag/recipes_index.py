from __future__ import annotations

import json
import logging
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
import re

from rank_bm25 import BM25Okapi

logger = logging.getLogger("rag")

_DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_EMBEDDINGS = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_data_path() -> Path:
    return _repo_root() / "data" / "recipes.jsonl"


def default_index_path() -> Path:
    return _repo_root() / "data" / "recipes.faiss"


def default_meta_path() -> Path:
    return _repo_root() / "data" / "recipes_meta.jsonl"


def default_bm25_path() -> Path:
    return _repo_root() / "data" / "recipes_bm25.pkl"


@dataclass
class RecipeDoc:
    id: str
    title: str
    text: str
    metadata: dict[str, Any]


def load_recipes(path: Path) -> list[RecipeDoc]:
    docs: list[RecipeDoc] = []
    if not path.exists():
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
            text = _doc_text(item)
            docs.append(
                RecipeDoc(
                    id=doc_id,
                    title=title,
                    text=text,
                    metadata=item if isinstance(item, dict) else {},
                )
            )
    return docs


def _doc_text(item: dict[str, Any]) -> str:
    parts: list[str] = []
    title = item.get("title") or item.get("name")
    if title:
        parts.append(str(title))
    tags = item.get("tags") or []
    if isinstance(tags, list):
        parts.extend([str(tag) for tag in tags if tag])
    ingredients = item.get("ingredients") or item.get("items")
    if isinstance(ingredients, list):
        parts.extend([str(ing) for ing in ingredients if ing])
    summary = item.get("summary") or item.get("description")
    if summary:
        parts.append(str(summary))
    return " ".join(parts)


def tokenize(text: str) -> list[str]:
    if not text:
        return []
    return re.findall(r"[\u4e00-\u9fff]|[a-zA-Z0-9]+", text.lower())


def keyword_score(query: str, text: str) -> float:
    q_tokens = set(tokenize(query))
    if not q_tokens:
        return 0.0
    t_tokens = set(tokenize(text))
    if not t_tokens:
        return 0.0
    return float(len(q_tokens & t_tokens)) / float(len(q_tokens))


def load_faiss_index(path: Path):
    import faiss  # type: ignore

    if not path.exists():
        return None
    return faiss.read_index(str(path))


def save_faiss_index(index, path: Path) -> None:
    import faiss  # type: ignore

    path.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(path))


def save_metadata(docs: Iterable[RecipeDoc], path: Path) -> None:
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


def load_metadata(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
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


def build_faiss_index(
    docs: list[RecipeDoc],
    model_name: str = _DEFAULT_MODEL,
):
    import numpy as np  # type: ignore
    import faiss  # type: ignore

    embeddings_model = get_embedding_model(model_name)
    vectors = embeddings_model.embed_documents([doc.text for doc in docs])
    embeddings = np.asarray(vectors, dtype="float32")
    index = faiss.IndexFlatIP(embeddings.shape[1])
    index.add(embeddings)
    return index


def build_bm25_index(docs: list[RecipeDoc]) -> BM25Okapi:
    """Build BM25 index from recipe documents."""
    tokenized_corpus = [tokenize(doc.text) for doc in docs]
    return BM25Okapi(tokenized_corpus)


def save_bm25_index(bm25: BM25Okapi, path: Path) -> None:
    """Save BM25 index to file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(bm25, f)
    logger.info("BM25 index saved to %s", path)


def load_bm25_index(path: Path) -> BM25Okapi | None:
    """Load BM25 index from file."""
    if not path.exists():
        logger.warning("BM25 index not found: %s", path)
        return None
    try:
        with path.open("rb") as f:
            return pickle.load(f)
    except Exception as e:
        logger.error("Failed to load BM25 index: %s", e)
        return None


def get_embedding_model(model_name: str | None = None):
    global _EMBEDDINGS
    if _EMBEDDINGS is not None:
        return _EMBEDDINGS
    
    import warnings
    # Suppress LangChain deprecation warnings for cleaner logs
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        from langchain_community.embeddings import HuggingFaceEmbeddings  # type: ignore
        _EMBEDDINGS = HuggingFaceEmbeddings(
            model_name=model_name or _DEFAULT_MODEL,
            encode_kwargs={"normalize_embeddings": True},
        )
    return _EMBEDDINGS


def warmup(model_name: str | None = None) -> None:
    """Preload embedding model to avoid cold start latency."""
    import warnings
    logger.info("🚀 RAG warmup started...")
    try:
        # Suppress verbose model loading logs
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            warnings.filterwarnings("ignore", category=FutureWarning)
            model = get_embedding_model(model_name)
            model.embed_query("warmup")
        logger.info("✅ RAG warmup completed - embedding model ready")
    except Exception as e:
        logger.warning("⚠️ RAG warmup skipped: %s", e)
