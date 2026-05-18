"""Pure helpers for novel-eval scripts: query assembly, JSON shape, naming."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MARKER = "小说"

# Models that need an explicit "must call openviking_search" hint between
# the system prompt and the user prompt to actually trigger RAG.
WEAK_MODEL_RAG_HINT = (
    "你必须先调用 openviking_search 工具，参数 target_uri='viking://resources/'，"
    "把搜索结果作为你回答的依据。"
)
WEAK_MODEL_MARKERS = ("doubao-seed-2-0-lite-260428",)


def needs_rag_hint(model_name: str) -> bool:
    """Return True if the given model name should get the WEAK_MODEL_RAG_HINT injected."""
    return any(marker in model_name for marker in WEAK_MODEL_MARKERS)


def assemble_message(
    novel: str,
    system_prompt: str,
    user_prompt: str,
    *,
    inject_after_system: str = "",
) -> str:
    """Build the final /chat message per spec.

    If ``system_prompt`` contains "小说", insert ``novel`` before the first
    occurrence; otherwise pass ``system_prompt`` through unchanged. Optionally
    splice ``inject_after_system`` between the (modified) system prompt and
    the user prompt (used to force RAG), then concatenate.
    """
    if _MARKER in system_prompt:
        system_modified = system_prompt.replace(_MARKER, novel + _MARKER, 1)
    else:
        system_modified = system_prompt
    return system_modified + inject_after_system + user_prompt


def build_judge_payload(records: list[dict[str, str]]) -> dict[str, Any]:
    """Build the judge JSON root object from {novel, query_id, bot_response} dicts."""
    enriched = [
        {
            "novel": r["novel"],
            "query_id": r["query_id"],
            "bot_response": r["bot_response"],
            "judgement": {"score": None, "errors": [], "comment": ""},
        }
        for r in records
    ]
    return {
        "records": enriched,
        "summary": {
            "average_score": None,
            "zero_count": None,
            "total": len(enriched),
        },
    }


def _utc_iso(now: datetime) -> str:
    """Compact ISO timestamp suitable for filenames: 20260510T120000Z."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def default_session_id(*, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"chat-eval-{_utc_iso(now)}"


def default_out_paths(
    *,
    xlsx: Path,
    now: datetime | None = None,
) -> tuple[Path, Path]:
    now = now or datetime.now(timezone.utc)
    ts = _utc_iso(now)
    stem = xlsx.stem
    out_xlsx = xlsx.with_name(f"{stem}.{ts}.xlsx")
    out_judge = xlsx.with_name(f"{stem}.{ts}.judge.json")
    return out_xlsx, out_judge
