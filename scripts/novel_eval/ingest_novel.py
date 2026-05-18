"""Ingest a single novel markdown file into OpenViking.

NOTE: For the ov_flattern experiment, prefer ``scripts/flatten_ingest.py`` —
it skips the VLM SemanticQueue entirely (no abstract/overview), writes flat
chunks under viking://resources/ with the experiment naming convention,
and tracks per-novel duration + embedding token usage. This file is
retained for parity with the baseline ingest path.

The user API key alone is enough — the server routes the request to its
owning account/user, so no account-id/user-id args are required.

Usage:
    python scripts/novel_eval/ingest_novel.py \\
        --md /abs/path/to/novel.md \\
        --user-api-key <USER_KEY> \\
        [--base-url http://127.0.0.1:1934] \\
        [--to viking://resources/novels/<filename>] \\
        [--source-name <name>] \\
        [--timeout 3600]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from scripts.novel_eval._common import OvClient, OvHttpError

logger = logging.getLogger("ingest_novel")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingest a novel markdown into OpenViking.")
    p.add_argument("--md", required=True, type=Path, help="Path to a single .md file")
    p.add_argument("--user-api-key", required=True, help="OpenViking user API key")
    p.add_argument("--base-url", default="http://127.0.0.1:1934")
    p.add_argument(
        "--to",
        default=None,
        help="Destination URI; default viking://resources/novels/<md.stem>",
    )
    p.add_argument(
        "--source-name",
        default=None,
        help="Override source_name; default uses md filename",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=3600,
        help="add_resource server-side wait timeout (s); default 1 hour",
    )
    return p.parse_args()


def _format_telemetry(telemetry: dict[str, Any]) -> str:
    """One-line digest of duration + token usage."""
    duration_ms = telemetry.get("duration_ms", 0.0)
    tokens = telemetry.get("tokens") or {}
    llm = tokens.get("llm") or {}
    emb = tokens.get("embedding") or {}
    rerank = tokens.get("rerank") or {}
    return (
        f"duration={duration_ms / 1000.0:.2f}s "
        f"tokens_total={tokens.get('total', 0)} "
        f"llm(in/out/total)={llm.get('input', 0)}/{llm.get('output', 0)}/{llm.get('total', 0)} "
        f"embedding={emb.get('total', 0)} "
        f"rerank={rerank.get('total', 0)}"
    )


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    md: Path = args.md
    if not md.is_file():
        logger.error("md file not found: %s", md)
        return 2
    if md.suffix.lower() != ".md":
        logger.error("expected .md file, got: %s", md.suffix)
        return 2

    to = args.to or f"viking://resources/novels/{md.stem}"
    source_name = args.source_name or md.name

    # Client timeout must outlive the server-side wait budget.
    with OvClient(
        base_url=args.base_url,
        user_api_key=args.user_api_key,
        timeout=float(args.timeout) + 60.0,
    ) as client:
        try:
            with md.open("rb") as f:
                upload = client.post_multipart(
                    "/api/v1/resources/temp_upload",
                    files={"file": (md.name, f, "text/markdown")},
                    data={"upload_mode": "local", "telemetry": "false"},
                )
        except OvHttpError as e:
            logger.error("temp_upload failed: %s", e)
            return 1

        temp_file_id = (upload.get("result") or {}).get("temp_file_id") or upload.get(
            "temp_file_id"
        )
        if not temp_file_id:
            logger.error("temp_upload response missing temp_file_id: %s", upload)
            return 1
        logger.info("uploaded temp_file_id=%s", temp_file_id)

        t0 = time.monotonic()
        try:
            response = client.post_json(
                "/api/v1/resources",
                json={
                    "temp_file_id": temp_file_id,
                    "to": to,
                    "source_name": source_name,
                    "wait": True,
                    "timeout": args.timeout,
                    "create_parent": True,
                    "strict": True,
                    "telemetry": True,
                },
            )
        except OvHttpError as e:
            logger.error("add_resource failed: %s", e)
            return 1
        wall_s = time.monotonic() - t0

    telemetry = response.get("telemetry") or {}
    result = response.get("result", response)

    logger.info("ingest done: to=%s wall=%.2fs", to, wall_s)
    if telemetry:
        logger.info("telemetry: %s", _format_telemetry(telemetry))
    else:
        logger.warning("telemetry block missing from response")

    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0 if result else 1


if __name__ == "__main__":
    sys.exit(main())
