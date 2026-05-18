from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.novel_eval._query import (
    WEAK_MODEL_RAG_HINT,
    assemble_message,
    build_judge_payload,
    default_out_paths,
    default_session_id,
    needs_rag_hint,
)


class TestAssembleMessage:
    def test_inserts_novel_before_first_xiaoshuo(self):
        msg = assemble_message(
            novel="三体",
            system_prompt="你是这部小说的资深读者，请回答关于小说的问题。",
            user_prompt="请总结第一章。",
        )
        assert msg == ("你是这部三体小说的资深读者，请回答关于小说的问题。请总结第一章。")

    def test_raises_when_marker_missing(self):
        with pytest.raises(ValueError, match="小说"):
            assemble_message(
                novel="三体",
                system_prompt="你是资深读者。",
                user_prompt="请总结。",
            )

    def test_no_separator_between_system_and_user(self):
        msg = assemble_message("X", "讲讲这部小说。", "为什么？")
        assert msg == "讲讲这部X小说。为什么？"

    def test_inject_after_system_spliced_between_system_and_user(self):
        msg = assemble_message(
            "三体",
            "你是这部小说的读者。",
            "请总结。",
            inject_after_system="MUST_RAG.",
        )
        assert msg == "你是这部三体小说的读者。MUST_RAG.请总结。"


class TestNeedsRagHint:
    def test_matches_lite_model(self):
        assert needs_rag_hint("doubao-seed-2-0-lite-260428") is True
        assert needs_rag_hint("volcengine/doubao-seed-2-0-lite-260428") is True

    def test_does_not_match_pro_or_other(self):
        assert needs_rag_hint("doubao-seed-2-0-pro-260215") is False
        assert needs_rag_hint("gpt-5.4") is False
        assert needs_rag_hint("") is False

    def test_hint_text_targets_resources_uri(self):
        assert "openviking_search" in WEAK_MODEL_RAG_HINT
        assert "viking://resources/" in WEAK_MODEL_RAG_HINT


class TestBuildJudgePayload:
    def test_skeleton_with_two_records(self):
        payload = build_judge_payload(
            records=[
                {"novel": "三体", "query_id": "row_2", "bot_response": "ans 1"},
                {"novel": "三体", "query_id": "row_3", "bot_response": "ans 2"},
            ]
        )
        assert payload == {
            "records": [
                {
                    "novel": "三体",
                    "query_id": "row_2",
                    "bot_response": "ans 1",
                    "judgement": {"score": None, "errors": [], "comment": ""},
                },
                {
                    "novel": "三体",
                    "query_id": "row_3",
                    "bot_response": "ans 2",
                    "judgement": {"score": None, "errors": [], "comment": ""},
                },
            ],
            "summary": {
                "average_score": None,
                "zero_count": None,
                "total": 2,
            },
        }

    def test_empty_records(self):
        payload = build_judge_payload(records=[])
        assert payload["records"] == []
        assert payload["summary"]["total"] == 0


class TestDefaults:
    def test_session_id_uses_utc_iso(self):
        sid = default_session_id(
            now=datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc),
        )
        assert sid == "chat-eval-20260510T120000Z"

    def test_out_paths_built_from_input(self, tmp_path: Path):
        xlsx = tmp_path / "queries.xlsx"
        xlsx.write_bytes(b"")
        out_xlsx, out_judge = default_out_paths(
            xlsx=xlsx,
            now=datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc),
        )
        assert out_xlsx == tmp_path / "queries.20260510T120000Z.xlsx"
        assert out_judge == tmp_path / "queries.20260510T120000Z.judge.json"
