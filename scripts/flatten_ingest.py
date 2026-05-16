"""Flat ingest script for the ov_flattern experiment.

Walks the three target novels' markdown files, detects chapters, chunks them,
and writes them flat under viking://resource/. Each chunk goes through
``VikingFS.write_file`` and is then enqueued to the EMBEDDING queue with a
``Context`` whose ``level=DETAIL`` and ``abstract=""``.

VLM-driven abstract/overview generation is bypassed entirely: the SemanticQueue
is never enqueued. At retrieval time there are no L0/L1 records, so the
hierarchical retriever falls back to pure L2 vector matching.

Usage:
    # Preview only:
    python scripts/flatten_ingest.py --dry-run

    # Real ingest (requires server running with ov-flattern.conf):
    python scripts/flatten_ingest.py \
        --account-id ACC --user-id USR \
        --novels 神雕侠侣 仙逆 诛仙
"""

import argparse
import asyncio
from pathlib import Path
from typing import Any, Optional

from scripts._flatten_chunker import chunk_chapter, locate_chapters
from scripts._flatten_uri import build_flat_uri

NOVEL_MAP = {
    "神雕侠侣": "/Users/bytedance/Documents/byterec/viking_evals/神雕侠侣.md",
    "仙逆": "/Users/bytedance/Documents/byterec/viking_evals/仙逆.md",
    "诛仙": "/Users/bytedance/Documents/byterec/viking_evals/诛仙.md",
}


async def _real_ingest_chunk(
    viking_fs: Any,
    embedding_queue: Any,
    *,
    uri: str,
    content: str,
    account_id: str,
    user_id: str,
) -> None:
    """Write one chunk to AGFS and enqueue its embedding."""
    # Local imports so that unit tests that mock both args don't need the live
    # OpenViking runtime imported at module-load time.
    from openviking.core.context import Context, ContextLevel, Vectorize
    from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter

    await viking_fs.write_file(uri, content)

    ctx_obj = Context(
        uri=uri,
        level=ContextLevel.DETAIL,
        is_leaf=True,
        abstract="",
        account_id=account_id,
    )
    ctx_obj.set_vectorize(Vectorize(text=content))
    msg = EmbeddingMsgConverter.from_context(ctx_obj)
    if msg is not None:
        await embedding_queue.enqueue(msg)


async def ingest_one(
    novel: str,
    md_path: Path,
    dry_run: bool,
    viking_fs: Optional[Any] = None,
    embedding_queue: Optional[Any] = None,
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
                assert viking_fs is not None and embedding_queue is not None
                assert account_id is not None and user_id is not None
                await _real_ingest_chunk(
                    viking_fs,
                    embedding_queue,
                    uri=uri,
                    content=part,
                    account_id=account_id,
                    user_id=user_id,
                )
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
    parser.add_argument(
        "--account-id",
        help="Account ID for the Context (required for real ingest).",
    )
    parser.add_argument(
        "--user-id",
        help="User ID for the Context (required for real ingest).",
    )
    args = parser.parse_args()

    viking_fs: Optional[Any] = None
    embedding_queue: Optional[Any] = None

    if not args.dry_run:
        if not args.account_id or not args.user_id:
            parser.error("--account-id and --user-id are required when not --dry-run")
        from openviking.storage.queuefs.queue_manager import get_queue_manager
        from openviking.storage.viking_fs import get_viking_fs

        viking_fs = get_viking_fs()
        embedding_queue = get_queue_manager().get_queue("EMBEDDING")

    grand_total = 0
    for novel in args.novels:
        md_path = Path(NOVEL_MAP[novel])
        n = await ingest_one(
            novel,
            md_path,
            args.dry_run,
            viking_fs=viking_fs,
            embedding_queue=embedding_queue,
            account_id=args.account_id,
            user_id=args.user_id,
        )
        print(f"[{novel}] {n} chunks")
        grand_total += n
    print(f"TOTAL: {grand_total} chunks")


if __name__ == "__main__":
    asyncio.run(main())
