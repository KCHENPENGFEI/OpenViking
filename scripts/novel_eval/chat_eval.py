"""Run Excel-driven evaluation queries against VikingBot via OpenViking proxy.

Reads queries from an .xlsx (col1=novel, col3=system_prompt, col4=user_prompt),
calls POST /bot/v1/chat concurrently (controlled by --concurrency) under a
single shared session, writes responses (with per-row metrics suffix) to a
caller-chosen column of an output .xlsx, and emits a judge JSON (no query
content, no metrics) for LLM-as-judge.

If --out-xlsx already exists, it is opened for in-place editing so a single
workbook can accumulate columns across model runs. A trailing row with col 1
== ``__AVERAGE__`` is appended carrying average duration / token usage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from openpyxl import load_workbook

from scripts.novel_eval._common import AsyncOvClient, OvHttpError
from scripts.novel_eval._query import (
    WEAK_MODEL_RAG_HINT,
    assemble_message,
    build_judge_payload,
    default_out_paths,
    default_session_id,
)

logger = logging.getLogger("chat_eval")

_AVG_ROW_SENTINEL = "__AVERAGE__"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run Excel queries against VikingBot.")
    p.add_argument("--xlsx", required=True, type=Path, help="Input Excel path")
    p.add_argument("--user-api-key", required=True)
    p.add_argument(
        "--user-id",
        required=True,
        help="Vikingbot sender id — written into the chat body's user_id field "
        "and used by the bot as the memory namespace owner (viking://user/<id>/memories/). "
        "Mirrors `ov chat --sender`. NOT auto-derived from --user-api-key.",
    )
    p.add_argument(
        "--account-id",
        default=None,
        help="OpenViking account id — sent as ?account_id=... query param to scope "
        "OV auth. Optional; mirrors `ov chat --account` semantically.",
    )
    p.add_argument(
        "--model-name",
        required=True,
        help="Model name vikingbot is running; written as the response column header "
        "and used to decide whether to inject the weak-model RAG hint.",
    )
    p.add_argument(
        "--response-col",
        type=int,
        default=8,
        help="1-indexed column in the output xlsx for the bot response "
        "(default 8). Row 1 of this column gets the model name as header.",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Number of concurrent /chat requests in flight (default 1 = sequential).",
    )
    p.add_argument("--base-url", default="http://127.0.0.1:1934")
    p.add_argument(
        "--session-id",
        default="auto",
        help="Session id; 'auto' = chat-eval-<utc-iso>. Shared across all rows in a run.",
    )
    p.add_argument("--channel-id", default=None)
    p.add_argument("--out-xlsx", default=None, type=Path)
    p.add_argument("--out-judge", default=None, type=Path)
    p.add_argument("--start-row", type=int, default=2)
    p.add_argument("--end-row", type=int, default=-1, help="-1 = last row")
    p.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="Per-worker delay after each row (seconds); throttle under concurrency.",
    )
    p.add_argument("--timeout", type=int, default=300)
    return p.parse_args()


def _row_value(row: tuple[Any, ...], idx_one_based: int) -> str:
    if idx_one_based - 1 >= len(row):
        return ""
    v = row[idx_one_based - 1]
    if v is None:
        return ""
    return str(v)


def _persist(
    wb: Any,
    out_xlsx: Path,
    judge_records: list[dict[str, str]],
    out_judge: Path,
) -> None:
    """Flush xlsx + judge JSON to disk so the run survives a crash mid-way.

    Logs the resulting file size so the operator can see at a glance that
    writes are landing (and the file isn't stuck at 0 bytes).
    """
    try:
        wb.save(out_xlsx)
    except Exception:
        logger.exception("xlsx save FAILED: %s", out_xlsx)
        raise
    out_judge.write_text(
        json.dumps(build_judge_payload(judge_records), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        size = out_xlsx.stat().st_size
    except OSError:
        size = -1
    logger.debug(
        "persisted xlsx=%s (%d bytes) judge_records=%d",
        out_xlsx,
        size,
        len(judge_records),
    )


def _format_cell_with_metrics(
    bot_msg: str,
    duration_s: float,
    token_usage: Optional[dict],
    iteration: Optional[int],
    tools_used_names: Optional[list[str]],
    time_cost: Optional[float] = None,
) -> str:
    if token_usage:
        p = token_usage.get("prompt_tokens", 0)
        c = token_usage.get("completion_tokens", 0)
        t = token_usage.get("total_tokens", 0)
        tok_part = f"tokens(p/c/t)={p}/{c}/{t}"
    else:
        tok_part = "tokens=N/A"
    iter_part = f"iter={iteration}" if iteration is not None else "iter=N/A"
    if tools_used_names is not None:
        tools_part = f"tools=[{','.join(tools_used_names) if tools_used_names else ''}]"
    else:
        tools_part = "tools=N/A"
    srv_part = f"srv_time={time_cost:.2f}s" if time_cost is not None else "srv_time=N/A"
    return (
        f"{bot_msg}\n\n[metrics] dur={duration_s:.2f}s {srv_part} "
        f"{tok_part} {iter_part} {tools_part}"
    )


def _format_avg(metrics: list[dict[str, Any]]) -> str:
    if not metrics:
        return "[metrics-avg] (no successful rows)"
    n = len(metrics)
    dur_avg = sum(m["duration_s"] for m in metrics) / n
    have_tokens = [m for m in metrics if m.get("token_usage")]
    if have_tokens:
        p_avg = sum(m["token_usage"].get("prompt_tokens", 0) for m in have_tokens) / len(
            have_tokens
        )
        c_avg = sum(m["token_usage"].get("completion_tokens", 0) for m in have_tokens) / len(
            have_tokens
        )
        t_avg = sum(m["token_usage"].get("total_tokens", 0) for m in have_tokens) / len(have_tokens)
        tok_part = (
            f"tokens_avg(p/c/t)={p_avg:.1f}/{c_avg:.1f}/{t_avg:.1f} (n_tok={len(have_tokens)})"
        )
    else:
        tok_part = "tokens_avg=N/A"

    have_iter = [m for m in metrics if m.get("iteration") is not None]
    if have_iter:
        iter_avg = sum(m["iteration"] for m in have_iter) / len(have_iter)
        iter_part = f"iter_avg={iter_avg:.2f} (n_iter={len(have_iter)})"
    else:
        iter_part = "iter_avg=N/A"

    # Frequency of each tool across all rows that reported tools_used_names
    tool_counts: dict[str, int] = {}
    rows_with_tools = 0
    for m in metrics:
        tools = m.get("tools_used_names")
        if tools is None:
            continue
        rows_with_tools += 1
        for t in tools:
            tool_counts[t] = tool_counts.get(t, 0) + 1
    if rows_with_tools:
        tools_breakdown = ",".join(f"{name}:{cnt}" for name, cnt in sorted(tool_counts.items()))
        tools_part = f"tools=[{tools_breakdown}] (n_rows_with_tools={rows_with_tools})"
    else:
        tools_part = "tools=N/A"

    have_srv_time = [m for m in metrics if m.get("time_cost") is not None]
    if have_srv_time:
        srv_avg = sum(m["time_cost"] for m in have_srv_time) / len(have_srv_time)
        srv_part = f"srv_time_avg={srv_avg:.2f}s (n_srv={len(have_srv_time)})"
    else:
        srv_part = "srv_time_avg=N/A"

    return (
        f"[metrics-avg] dur_avg={dur_avg:.2f}s {srv_part} {tok_part} {iter_part} {tools_part} n={n}"
    )


async def _process_row(
    *,
    row_num: int,
    novel: str,
    system_prompt: str,
    user_prompt: str,
    inject: str,
    session_id: str,
    user_id: str,
    channel_id: Optional[str],
    client: AsyncOvClient,
    sem: asyncio.Semaphore,
    persist_lock: asyncio.Lock,
    ws: Any,
    response_col: int,
    judge_records: list[dict[str, str]],
    metrics: list[dict[str, Any]],
    wb: Any,
    out_xlsx: Path,
    out_judge: Path,
    sleep_after: float,
) -> bool:
    """Process one row. Returns True on failure (so caller can count failures).

    Wraps the whole body in a broad try/except so an unexpected exception in
    one row doesn't poison asyncio.gather and abort sibling rows.
    """
    try:
        return await _process_row_inner(
            row_num=row_num,
            novel=novel,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            inject=inject,
            session_id=session_id,
            user_id=user_id,
            channel_id=channel_id,
            client=client,
            sem=sem,
            persist_lock=persist_lock,
            ws=ws,
            response_col=response_col,
            judge_records=judge_records,
            metrics=metrics,
            wb=wb,
            out_xlsx=out_xlsx,
            out_judge=out_judge,
            sleep_after=sleep_after,
        )
    except Exception as exc:
        logger.exception("row=%d unexpected error: %s", row_num, exc)
        msg = f"[ERROR] unexpected: {type(exc).__name__}: {exc}"
        try:
            async with persist_lock:
                ws.cell(row=row_num, column=response_col, value=msg)
                judge_records.append(
                    {"novel": novel, "query_id": f"row_{row_num}", "bot_response": msg}
                )
                _persist(wb, out_xlsx, judge_records, out_judge)
        except Exception:
            logger.exception("row=%d failed to even record the failure", row_num)
        return True


async def _process_row_inner(
    *,
    row_num: int,
    novel: str,
    system_prompt: str,
    user_prompt: str,
    inject: str,
    session_id: str,
    user_id: str,
    channel_id: Optional[str],
    client: AsyncOvClient,
    sem: asyncio.Semaphore,
    persist_lock: asyncio.Lock,
    ws: Any,
    response_col: int,
    judge_records: list[dict[str, str]],
    metrics: list[dict[str, Any]],
    wb: Any,
    out_xlsx: Path,
    out_judge: Path,
    sleep_after: float,
) -> bool:
    async with sem:
        try:
            final_message = assemble_message(
                novel, system_prompt, user_prompt, inject_after_system=inject
            )
            logger.info(f"final_message in prompt: {final_message}")
        except ValueError as e:
            msg = f"[ERROR] {e}"
            logger.warning("row=%d %s", row_num, msg)
            async with persist_lock:
                ws.cell(row=row_num, column=response_col, value=msg)
                judge_records.append(
                    {"novel": novel, "query_id": f"row_{row_num}", "bot_response": msg}
                )
                _persist(wb, out_xlsx, judge_records, out_judge)
            return True

        body: dict[str, Any] = {
            "message": final_message,
            "session_id": session_id,
            "user_id": user_id,
        }
        if channel_id:
            body["channel_id"] = channel_id

        t0 = time.monotonic()
        logger.info(
            "row=%d novel=%s msg_len=%d -> /chat",
            row_num,
            novel,
            len(final_message),
        )
        bot_msg: str
        token_usage: Optional[dict] = None
        iteration: Optional[int] = None
        tools_used_names: Optional[list[str]] = None
        time_cost: Optional[float] = None
        failed = False
        try:
            resp = await client.post_json("/bot/v1/chat", json=body)
            bot_msg = resp.get("message") or ""
            token_usage = resp.get("token_usage")
            iteration = resp.get("iteration")
            tools_used_names = resp.get("tools_used_names")
            time_cost = resp.get("time_cost")
            if not bot_msg:
                bot_msg = f"[ERROR] empty response: {json.dumps(resp, ensure_ascii=False)[:200]}"
                failed = True
        except OvHttpError as e:
            bot_msg = f"[ERROR] {e}"
            failed = True

        duration_s = time.monotonic() - t0
        logger.info(
            "row=%d done in %.2fs (srv=%s, resp_len=%d, tokens=%s, iter=%s, tools=%s)",
            row_num,
            duration_s,
            time_cost,
            len(bot_msg),
            token_usage,
            iteration,
            tools_used_names,
        )

        cell_value = _format_cell_with_metrics(
            bot_msg, duration_s, token_usage, iteration, tools_used_names, time_cost
        )

        async with persist_lock:
            ws.cell(row=row_num, column=response_col, value=cell_value)
            # Judge JSON: only the raw bot_response, no metrics — keeps the
            # downstream LLM-as-judge prompt focused on answer quality.
            judge_records.append(
                {"novel": novel, "query_id": f"row_{row_num}", "bot_response": bot_msg}
            )
            if not failed:
                metrics.append(
                    {
                        "duration_s": duration_s,
                        "time_cost": time_cost,
                        "token_usage": token_usage,
                        "iteration": iteration,
                        "tools_used_names": tools_used_names,
                    }
                )
            _persist(wb, out_xlsx, judge_records, out_judge)

        if sleep_after > 0:
            await asyncio.sleep(sleep_after)
        return failed


async def _run(args: argparse.Namespace) -> int:
    xlsx: Path = args.xlsx
    if not xlsx.is_file():
        logger.error("xlsx not found: %s", xlsx)
        return 2
    if args.concurrency < 1:
        logger.error("--concurrency must be >= 1")
        return 2

    now = datetime.now(timezone.utc)
    session_id = default_session_id(now=now) if args.session_id == "auto" else args.session_id
    out_xlsx_default, out_judge_default = default_out_paths(xlsx=xlsx, now=now)
    out_xlsx: Path = args.out_xlsx or out_xlsx_default
    out_judge: Path = args.out_judge or out_judge_default

    if out_xlsx.exists():
        logger.info("out_xlsx exists, editing in place: %s", out_xlsx)
        wb = load_workbook(out_xlsx)
    else:
        logger.info("out_xlsx does not exist, seeding from input: %s -> %s", xlsx, out_xlsx)
        shutil.copy(xlsx, out_xlsx)
        wb = load_workbook(out_xlsx)
    ws = wb.active

    # Header row of the response column = model name.
    ws.cell(row=1, column=args.response_col, value=args.model_name)

    # Always inject the RAG hint regardless of model — even stronger models
    # benefit from an explicit "must call openviking_search" instruction when
    # the query topic is something the model knows from pretraining.
    inject = WEAK_MODEL_RAG_HINT
    logger.info(
        "model=%s → injecting RAG hint between system/user (always-on)",
        args.model_name,
    )

    end_row = ws.max_row if args.end_row == -1 else args.end_row
    if end_row < args.start_row:
        logger.warning("no rows to process: start=%d end=%d", args.start_row, end_row)
        _persist(wb, out_xlsx, [], out_judge)
        return 0

    # Collect data rows up front; skip empty rows and previous average rows.
    rows_to_process: list[tuple[int, str, str, str]] = []
    for row_num in range(args.start_row, end_row + 1):
        row = tuple(ws.cell(row=row_num, column=c).value for c in range(1, 9))
        novel = _row_value(row, 1).strip()
        if novel.startswith("__"):  # sentinel from prior run (e.g. __AVERAGE__)
            continue
        system_prompt = _row_value(row, 3)
        user_prompt = _row_value(row, 4)
        if not (novel or system_prompt or user_prompt):
            continue
        rows_to_process.append((row_num, novel, system_prompt, user_prompt))

    judge_records: list[dict[str, str]] = []
    metrics: list[dict[str, Any]] = []
    # Empty skeleton on disk before first task fires.
    _persist(wb, out_xlsx, judge_records, out_judge)

    if not rows_to_process:
        logger.warning("no non-empty data rows in [%d, %d]", args.start_row, end_row)
        return 0

    sem = asyncio.Semaphore(args.concurrency)
    persist_lock = asyncio.Lock()
    logger.info(
        "starting %d rows with concurrency=%d session=%s",
        len(rows_to_process),
        args.concurrency,
        session_id,
    )

    async with AsyncOvClient(
        base_url=args.base_url,
        user_api_key=args.user_api_key,
        account_id=args.account_id,
        timeout=float(args.timeout),
    ) as client:
        tasks = [
            _process_row(
                row_num=row_num,
                novel=novel,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                inject=inject,
                session_id=session_id,
                user_id=args.user_id,
                channel_id=args.channel_id,
                client=client,
                sem=sem,
                persist_lock=persist_lock,
                ws=ws,
                response_col=args.response_col,
                judge_records=judge_records,
                metrics=metrics,
                wb=wb,
                out_xlsx=out_xlsx,
                out_judge=out_judge,
                sleep_after=args.sleep,
            )
            for (row_num, novel, system_prompt, user_prompt) in rows_to_process
        ]
        failure_flags = await asyncio.gather(*tasks, return_exceptions=True)

    failures = 0
    for f in failure_flags:
        if isinstance(f, BaseException):
            logger.error("task raised: %r", f)
            failures += 1
        elif f:
            failures += 1

    # Place the average row immediately after the last DATA row we processed
    # — NOT ws.max_row, which can be pushed far down by trailing empty rows or
    # __AVERAGE__ rows left behind by previous runs on the same workbook. This
    # also lets multiple model runs share a single __AVERAGE__ row, each
    # writing its own metric column into it.
    last_data_row = max(r for r, *_ in rows_to_process)
    avg_row = last_data_row + 1
    existing_col1 = ws.cell(row=avg_row, column=1).value
    if existing_col1 is None or (isinstance(existing_col1, str) and existing_col1.startswith("__")):
        ws.cell(row=avg_row, column=1, value=_AVG_ROW_SENTINEL)
    ws.cell(row=avg_row, column=args.response_col, value=_format_avg(metrics))
    _persist(wb, out_xlsx, judge_records, out_judge)

    logger.info(
        "done: out_xlsx=%s out_judge=%s failures=%d total=%d avg_row=%d session=%s",
        out_xlsx,
        out_judge,
        failures,
        len(judge_records),
        avg_row,
        session_id,
    )
    return 0 if failures == 0 else 1


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
