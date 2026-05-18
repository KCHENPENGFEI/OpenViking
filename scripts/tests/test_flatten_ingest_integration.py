"""Integration-style tests for the real ingest path of flatten_ingest."""

import os
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest

from scripts.flatten_ingest import _real_ingest_chunk


@pytest.mark.asyncio
async def test_real_ingest_chunk_writes_file_and_enqueues():
    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    viking_fs = AsyncMock()
    embedding_queue = AsyncMock()

    ctx = RequestContext(user=UserIdentifier("acc_test", "user_test", "default"), role=Role.ROOT)

    await _real_ingest_chunk(
        viking_fs,
        embedding_queue,
        uri="viking://resources/神雕侠侣_第一回 风月无情_1.md",
        content="### 第一回 风月无情\n\n正文…",
        ctx=ctx,
    )

    # 1) AGFS write was called once with the expected URI, chunk content, and ctx
    viking_fs.write_file.assert_awaited_once_with(
        "viking://resources/神雕侠侣_第一回 风月无情_1.md",
        "### 第一回 风月无情\n\n正文…",
        ctx=ctx,
    )
    write_args, write_kwargs = viking_fs.write_file.await_args
    assert write_args[0] == "viking://resources/神雕侠侣_第一回 风月无情_1.md"
    assert "第一回 风月无情" in write_args[1]
    assert write_kwargs["ctx"] is ctx

    # 2) EmbeddingQueue.enqueue was called once with a message whose context_data
    #    reflects level=2 (DETAIL), abstract is empty, and account_id matches
    embedding_queue.enqueue.assert_awaited_once()
    (msg,), _ = embedding_queue.enqueue.await_args
    assert msg is not None
    cd = msg.context_data
    assert cd["level"] == 2
    assert cd.get("abstract", "") == ""
    assert cd["uri"].endswith("_第一回 风月无情_1.md")
    assert cd["account_id"] == "acc_test"


def _run_cli(args, env=None):
    """Run flatten_ingest.py as a subprocess and capture stderr/stdout/returncode."""
    proc = subprocess.run(
        [sys.executable, "scripts/flatten_ingest.py", *args],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[2]),
    )
    return proc


def test_cli_rejects_non_dryrun_without_config():
    """Non-dry-run without --config must fail loudly."""
    proc = _run_cli(["--novels", "神雕侠侣", "--account-id", "a", "--user-id", "u"])
    assert proc.returncode != 0
    assert "--config" in proc.stderr.lower() or "--config" in proc.stdout.lower()


def test_cli_rejects_nonexistent_config():
    """Non-dry-run with a bogus --config path must fail with a clear message."""
    proc = _run_cli(
        [
            "--novels",
            "神雕侠侣",
            "--config",
            "/tmp/this-config-does-not-exist.conf",
            "--account-id",
            "a",
            "--user-id",
            "u",
        ]
    )
    assert proc.returncode != 0
    # Either argparse exit message or our explicit "does not exist" string
    combined = proc.stderr + proc.stdout
    assert "does not exist" in combined or "no such" in combined.lower()
