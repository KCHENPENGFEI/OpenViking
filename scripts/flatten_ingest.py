"""Flat ingest script for the ov_flattern experiment.

Walks the three target novels' markdown files, detects chapters, chunks them,
and writes them flat under viking://resources/. Each chunk goes through
``VikingFS.write_file`` and is then enqueued to the EMBEDDING queue with a
``Context`` whose ``level=DETAIL`` and ``abstract=""``.

VLM-driven abstract/overview generation is bypassed entirely: the SemanticQueue
is never enqueued. At retrieval time there are no L0/L1 records, so the
hierarchical retriever falls back to pure L2 vector matching.

Runtime model:
    The script bootstraps its own ``OpenVikingService`` (which initializes
    AGFS / VikingDB / VikingFS / QueueManager and starts queue workers).
    For each novel, the script enqueues the novel's chunks, drains the
    EMBEDDING queue, records (duration, token consumption) for that novel,
    then proceeds to the next novel. After all novels are done, it shuts
    down the service cleanly.

    IMPORTANT: stop the OpenViking server before running this script. Both
    processes opening the same workspace concurrently can cause SQLite queue
    and AGFS lock conflicts (the data-dir lock will reject the second
    process, so the failure is loud, not silent). After ingest completes,
    start the server (with the same ``--config``) to expose retrieval / chat
    APIs.

Config selection:
    The script uses the OpenViking config pointed to by ``--config`` (required
    for non-dry-run) by setting ``OPENVIKING_CONFIG_FILE`` before any
    openviking import. This prevents accidentally writing to the baseline
    workspace when the env var is missing in the shell.

Metrics:
    Per-novel ingest duration (wall-clock from enqueue start to drain end)
    and embedding prompt token consumption (delta of the embedder's process-
    wide TokenUsageTracker) are printed in a summary table at the end.
    Volcengine embedders report estimated tokens; the same estimator is used
    by the baseline so the numbers are directly comparable.

Usage:
    # Preview only (config optional, no service bootstrap):
    python scripts/flatten_ingest.py --dry-run

    # Real ingest (server MUST be stopped):
    python scripts/flatten_ingest.py \\
        --config ~/.openviking/ov-flattern.conf \\
        --account-id ACC --user-id USR \\
        --novels 神雕侠侣 仙逆 诛仙
"""

import argparse
import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from scripts._flatten_chunker import chunk_chapter, locate_chapters
from scripts._flatten_uri import build_flat_uri

NOVEL_MAP = {
    "神雕侠侣": "/Users/bytedance/Documents/byterec/viking_evals/神雕侠侣.md",
    "仙逆": "/Users/bytedance/Documents/byterec/viking_evals/仙逆.md",
    "诛仙": "/Users/bytedance/Documents/byterec/viking_evals/诛仙.md",
}


@dataclass
class NovelStats:
    """Per-novel ingest statistics."""

    chunks: int = 0
    duration_s: float = 0.0
    embed_tokens: int = 0


async def _real_ingest_chunk(
    viking_fs: Any,
    embedding_queue: Any,
    *,
    uri: str,
    content: str,
    account_id: str,
    user_id: str,
) -> None:
    """Write one chunk to AGFS and enqueue its embedding.

    Imports are deferred to keep unit-test setup lightweight.
    """
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


def _resolve_config_path(raw: str) -> str:
    """Expand ``~`` and return an absolute path."""
    return os.path.abspath(os.path.expanduser(raw))


def _print_summary(stats: Dict[str, NovelStats]) -> None:
    """Print a fixed-width per-novel summary table."""
    if not stats:
        return
    print()
    print("=== Ingest Summary ===")
    print(f"{'Novel':<14}{'Chunks':>8}{'Duration(s)':>14}{'EmbedTokens':>14}")
    total_chunks = 0
    total_duration = 0.0
    total_tokens = 0
    for novel, s in stats.items():
        print(f"{novel:<14}{s.chunks:>8}{s.duration_s:>14.2f}{s.embed_tokens:>14}")
        total_chunks += s.chunks
        total_duration += s.duration_s
        total_tokens += s.embed_tokens
    print("-" * 50)
    print(f"{'TOTAL':<14}{total_chunks:>8}{total_duration:>14.2f}{total_tokens:>14}")


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
        "--config",
        help=(
            "Path to OpenViking config file (e.g. ~/.openviking/ov-flattern.conf). "
            "REQUIRED for non-dry-run to prevent accidental writes to the baseline "
            "workspace. Optional for --dry-run."
        ),
    )
    parser.add_argument(
        "--account-id",
        help="Account ID for the Context (required for real ingest).",
    )
    parser.add_argument(
        "--user-id",
        help="User ID for the Context (required for real ingest).",
    )
    parser.add_argument(
        "--drain-timeout",
        type=float,
        default=None,
        help=(
            "Seconds to wait for the EMBEDDING queue to drain after each novel "
            "(default: unbounded). Only used for non-dry-run."
        ),
    )
    args = parser.parse_args()

    if not args.dry_run:
        if not args.config:
            parser.error(
                "--config is REQUIRED for non-dry-run ingest. "
                "Pass --config ~/.openviking/ov-flattern.conf (or your experiment config) "
                "to avoid writing into the baseline workspace by mistake."
            )
        if not args.account_id or not args.user_id:
            parser.error("--account-id and --user-id are required when not --dry-run")

    if args.config:
        resolved = _resolve_config_path(args.config)
        if not os.path.isfile(resolved):
            parser.error(f"--config path does not exist: {resolved}")
        os.environ["OPENVIKING_CONFIG_FILE"] = resolved
        print(f"[flatten_ingest] OPENVIKING_CONFIG_FILE = {resolved}")
    else:
        env_config = os.environ.get(
            "OPENVIKING_CONFIG_FILE",
            "(unset, would default to ~/.openviking/ov.conf)",
        )
        print(f"[flatten_ingest] (dry-run) OPENVIKING_CONFIG_FILE = {env_config}")

    viking_fs: Optional[Any] = None
    embedding_queue: Optional[Any] = None
    service: Optional[Any] = None
    queue_manager: Optional[Any] = None
    token_tracker: Optional[Any] = None

    if not args.dry_run:
        # Imports deferred until after env var is set so the config singleton
        # picks up the correct config file.
        from openviking.models.embedder.base import _get_token_tracker
        from openviking.service.core import OpenVikingService
        from openviking.storage.queuefs.queue_manager import get_queue_manager
        from openviking.storage.viking_fs import get_viking_fs

        print("[flatten_ingest] Initializing OpenVikingService...")
        service = OpenVikingService()
        await service.initialize()
        print("[flatten_ingest] OpenVikingService ready; embedding worker started.")

        viking_fs = get_viking_fs()
        queue_manager = get_queue_manager()
        embedding_queue = queue_manager.get_queue(queue_manager.EMBEDDING)
        token_tracker = _get_token_tracker()

    stats: Dict[str, NovelStats] = {}
    try:
        for novel in args.novels:
            md_path = Path(NOVEL_MAP[novel])

            if args.dry_run:
                n = await ingest_one(
                    novel,
                    md_path,
                    dry_run=True,
                )
                stats[novel] = NovelStats(chunks=n)
                print(f"[{novel}] {n} chunks")
                continue

            # Real-ingest path: serialize per novel so token deltas attribute correctly.
            assert token_tracker is not None and queue_manager is not None
            start_t = time.perf_counter()
            baseline_tokens = token_tracker.get_total_usage().prompt_tokens

            n = await ingest_one(
                novel,
                md_path,
                dry_run=False,
                viking_fs=viking_fs,
                embedding_queue=embedding_queue,
                account_id=args.account_id,
                user_id=args.user_id,
            )

            print(f"[{novel}] {n} chunks enqueued; waiting for EMBEDDING queue to drain...")
            await queue_manager.wait_complete(
                queue_name=queue_manager.EMBEDDING,
                timeout=args.drain_timeout,
            )

            duration = time.perf_counter() - start_t
            tokens = token_tracker.get_total_usage().prompt_tokens - baseline_tokens
            stats[novel] = NovelStats(chunks=n, duration_s=duration, embed_tokens=tokens)
            print(f"[{novel}] done: {n} chunks | {duration:.2f}s | {tokens} embed tokens")

        _print_summary(stats)
    finally:
        if service is not None:
            print("[flatten_ingest] Closing OpenVikingService...")
            await service.close()
            print("[flatten_ingest] Done.")


if __name__ == "__main__":
    asyncio.run(main())
