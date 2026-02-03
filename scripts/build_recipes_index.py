from __future__ import annotations

import argparse
from pathlib import Path

from app.agent.rag import recipes_index


def main() -> None:
    parser = argparse.ArgumentParser(description="Build FAISS index for recipes.jsonl")
    parser.add_argument(
        "--data",
        type=str,
        default=str(recipes_index.default_data_path()),
        help="Path to recipes.jsonl",
    )
    parser.add_argument(
        "--index",
        type=str,
        default=str(recipes_index.default_index_path()),
        help="Path to output FAISS index",
    )
    parser.add_argument(
        "--meta",
        type=str,
        default=str(recipes_index.default_meta_path()),
        help="Path to output metadata jsonl",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=recipes_index._DEFAULT_MODEL,
        help="SentenceTransformer model name",
    )
    parser.add_argument(
        "--bm25",
        type=str,
        default=str(recipes_index.default_bm25_path()),
        help="Path to output BM25 index",
    )
    args = parser.parse_args()

    data_path = Path(args.data)
    docs = recipes_index.load_recipes(data_path)
    if not docs:
        raise SystemExit(f"No recipes loaded from {data_path}")

    index = recipes_index.build_faiss_index(docs, model_name=args.model)
    recipes_index.save_faiss_index(index, Path(args.index))
    recipes_index.save_metadata(docs, Path(args.meta))
    
    # Build and save BM25 index
    bm25 = recipes_index.build_bm25_index(docs)
    recipes_index.save_bm25_index(bm25, Path(args.bm25))
    
    print(f"Indexed {len(docs)} recipes:")
    print(f"  FAISS -> {args.index}")
    print(f"  Meta  -> {args.meta}")
    print(f"  BM25  -> {args.bm25}")


if __name__ == "__main__":
    main()
