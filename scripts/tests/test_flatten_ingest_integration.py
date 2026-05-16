"""Integration-style tests for the real ingest path of flatten_ingest."""

from unittest.mock import AsyncMock

import pytest

from scripts.flatten_ingest import _real_ingest_chunk


@pytest.mark.asyncio
async def test_real_ingest_chunk_writes_file_and_enqueues():
    viking_fs = AsyncMock()
    embedding_queue = AsyncMock()

    await _real_ingest_chunk(
        viking_fs,
        embedding_queue,
        uri="viking://resource/神雕侠侣_第一回 风月无情_1.md",
        content="### 第一回 风月无情\n\n正文…",
        account_id="acc_test",
        user_id="user_test",
    )

    # 1) AGFS write was called once with the expected URI and chunk content
    viking_fs.write_file.assert_awaited_once()
    write_args, _ = viking_fs.write_file.await_args
    assert write_args[0] == "viking://resource/神雕侠侣_第一回 风月无情_1.md"
    assert "第一回 风月无情" in write_args[1]

    # 2) EmbeddingQueue.enqueue was called once with a message whose context_data
    #    reflects level=2 (DETAIL) and abstract is empty
    embedding_queue.enqueue.assert_awaited_once()
    (msg,), _ = embedding_queue.enqueue.await_args
    assert msg is not None
    cd = msg.context_data
    assert cd["level"] == 2
    assert cd.get("abstract", "") == ""
    assert cd["uri"].endswith("_第一回 风月无情_1.md")
