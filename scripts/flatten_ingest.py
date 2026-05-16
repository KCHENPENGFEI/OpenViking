"""Flat ingest script for the ov_flattern experiment.

Walks the three target novels' markdown files, detects chapters, chunks them,
and (in dry-run mode) prints the URIs that would be written. The real ingest
path is added in Task 2.2.

Usage:
    python scripts/flatten_ingest.py --dry-run
    python scripts/flatten_ingest.py --dry-run --novels 神雕侠侣
"""

import argparse
import asyncio
from pathlib import Path
from typing import Optional

from scripts._flatten_chunker import chunk_chapter, locate_chapters
from scripts._flatten_uri import build_flat_uri

NOVEL_MAP = {
    "神雕侠侣": "/Users/bytedance/Documents/byterec/viking_evals/神雕侠侣.md",
    "仙逆": "/Users/bytedance/Documents/byterec/viking_evals/仙逆.md",
    "诛仙": "/Users/bytedance/Documents/byterec/viking_evals/诛仙.md",
}


async def ingest_one(
    novel: str,
    md_path: Path,
    dry_run: bool,
    viking_fs: Optional[object] = None,
    embedding_queue: Optional[object] = None,
    account_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> int:
    """Ingest one novel. Returns the number of chunks produced."""
    content = md_path.read_text(encoding="utf-8")
    chapters = locate_chapters(content)
    total = 0
    for chap in chapters:
        parts = chunk_chapter(content, chap.chapter_idx)
        for i, part in enumerate(parts, 1):
            uri = build_flat_uri(novel, chap.volume, chap.chapter_title, i)
            if dry_run:
                preview = part.replace("\n", " ")[:80]
                print(f"[DRY] {uri}  ({len(part)} chars)  {preview}...")
            else:
                # Real ingest path is implemented in Task 2.2.
                raise NotImplementedError("real ingest pending Task 2.2")
            total += 1
    return total


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--novels",
        nargs="+",
        default=list(NOVEL_MAP.keys()),
        choices=list(NOVEL_MAP.keys()),
        help="Novels to ingest (default: all three).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview only; do not write.")
    args = parser.parse_args()

    grand_total = 0
    for novel in args.novels:
        md_path = Path(NOVEL_MAP[novel])
        n = await ingest_one(novel, md_path, args.dry_run)
        print(f"[{novel}] {n} chunks")
        grand_total += n
    print(f"TOTAL: {grand_total} chunks")


if __name__ == "__main__":
    asyncio.run(main())
