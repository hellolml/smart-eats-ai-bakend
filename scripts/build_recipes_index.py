"""
构建食谱索引脚本

用法：
    python scripts/build_recipes_index.py [--data PATH] [--index PATH] [--meta PATH] [--bm25 PATH]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from app.agent.rag import base as rag
from app.domain.recipe import recipe_index


def main() -> None:
    parser = argparse.ArgumentParser(description="Build indices for recipes")
    parser.add_argument(
        "--data",
        type=str,
        default=str(recipe_index.default_data_path()),
        help="Path to recipes.jsonl",
    )
    parser.add_argument(
        "--index",
        type=str,
        default=str(recipe_index.default_index_path()),
        help="Path to output FAISS index",
    )
    parser.add_argument(
        "--meta",
        type=str,
        default=str(recipe_index.default_meta_path()),
        help="Path to output metadata jsonl",
    )
    parser.add_argument(
        "--bm25",
        type=str,
        default=str(recipe_index.default_bm25_path()),
        help="Path to output BM25 index",
    )
    args = parser.parse_args()

    # Load recipes
    data_path = Path(args.data)
    docs = recipe_index.load_recipes(data_path)
    if not docs:
        raise SystemExit(f"No recipes loaded from {data_path}")

    print(f"Loaded {len(docs)} recipes")

    # Build and save FAISS index
    print("Building FAISS index...")
    faiss_index = recipe_index.build_faiss_index(docs)
    rag.save_faiss_index(faiss_index, Path(args.index))

    # Save metadata
    recipe_index.save_metadata(docs, Path(args.meta))

    # Build and save BM25 index
    print("Building BM25 index...")
    bm25_index = recipe_index.build_bm25_index(docs)
    rag.save_bm25_index(bm25_index, Path(args.bm25))

    print(f"\n✅ Indexed {len(docs)} recipes:")
    print(f"   FAISS -> {args.index}")
    print(f"   Meta  -> {args.meta}")
    print(f"   BM25  -> {args.bm25}")


if __name__ == "__main__":
    main()
