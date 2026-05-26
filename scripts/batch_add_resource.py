"""Batch `ov add-resource` over a large source directory.

Splits a source directory by its first-level entries (configurable depth)
and imports each as an independent `ov add-resource` call, so each batch
stays under the server's temp_upload size limit.

Usage:
    # Dry-run: just print the plan
    python scripts/batch_add_resource.py /path/to/big_dir \\
        --parent viking://resources/my_dest --dry-run

    # Real run, batches > 3 GB skipped (need to be split manually)
    python scripts/batch_add_resource.py /path/to/big_dir \\
        --parent viking://resources/my_dest \\
        --max-size 3221225472

    # Resume after partial run: skip entries whose target URI already exists
    python scripts/batch_add_resource.py /path/to/big_dir \\
        --parent viking://resources/my_dest \\
        --skip-existing

Behavior:
  * Source dir's direct children become individual batches.
  * Each batch gets target URI = <parent>/<entry_name> (name sanitized for URI safety).
  * Single-file children → file goes in directly; subdir children → that subdir goes in.
  * Oversized batches (> --max-size) are SKIPPED with a warning; rerun with
    --depth 2 (or higher) to split them further.
  * Failed batches are logged but the run continues.
  * Final summary lists totals + per-batch status.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def du_bytes(path: Path) -> int:
    """Sum of file sizes under path (recursive). Fast: stat-based, no shell out."""
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024
    return str(n)


def sanitize_segment(name: str) -> str:
    """Best-effort URI-safe segment; keep CJK + common ascii, fold whitespace."""
    out = []
    for ch in name:
        if ch.isspace():
            out.append("_")
        elif ch in "/\\:?#&%":
            out.append("_")
        else:
            out.append(ch)
    s = "".join(out).strip("._")
    return s or "unnamed"


def list_batches(source: Path, depth: int) -> list[Path]:
    """Enumerate batch units at the given depth under source."""
    if depth <= 1:
        return [p for p in sorted(source.iterdir()) if not p.name.startswith(".")]
    units: list[Path] = []
    for child in sorted(source.iterdir()):
        if child.name.startswith("."):
            continue
        if child.is_dir():
            units.extend(list_batches(child, depth - 1))
        else:
            units.append(child)
    return units


def run_cmd(cmd: list[str], env: dict, timeout: int) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            cmd, env=env, timeout=timeout,
            capture_output=True, text=True,
        )
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        return 124, e.stdout or "", (e.stderr or "") + "\n[script] timed out"


def target_exists(ov_bin: str, env: dict, uri: str, account: str | None, user: str | None) -> bool:
    cmd = [ov_bin, "stat", uri]
    if account:
        cmd += ["--account", account]
    if user:
        cmd += ["--user", user]
    rc, _, _ = run_cmd(cmd, env, timeout=30)
    return rc == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source", help="Source directory to import")
    ap.add_argument("--parent", required=True, help="Target parent URI (e.g. viking://resources/foo)")
    ap.add_argument(
        "--max-size", type=int, default=3 * 1024 * 1024 * 1024,
        help="Skip batches larger than this many bytes (default 3 GB)",
    )
    ap.add_argument(
        "--depth", type=int, default=1,
        help="Split granularity: 1=source's direct children (default); 2=grandchildren; ...",
    )
    ap.add_argument(
        "--port", type=int, default=1935,
        help="OpenViking server port (default 1935, matches ov-novel.conf)",
    )
    ap.add_argument(
        "--ov-bin",
        default=str(Path(__file__).resolve().parent.parent / ".venv-flattern/bin/ov"),
    )
    ap.add_argument("--account", default="qijing_ai_doubao_pro")
    ap.add_argument("--user", default="doubao_pro")
    ap.add_argument(
        "--api-key",
        default=os.environ.get(
            "OPENVIKING_API_KEY",
            "cWlqaW5nX2FpX2RvdWJhb19wcm8.ZG91YmFvX3Bybw.ZDVmZDg0ZTdiZmQ3ZWM1MzQwNGJhMDI1YmQ1NDZiMDIyYWZjZDNjNzkwN2ZjMDRkMjVmODJkNmUyNDEyMzBjMw",
        ),
    )
    ap.add_argument(
        "--per-batch-timeout", type=int, default=3600,
        help="Per-batch ov add-resource timeout in seconds (default 1h)",
    )
    ap.add_argument(
        "--skip-existing", action="store_true",
        help="Probe target URI via `ov stat` and skip if it already exists",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Print plan only, no `ov` calls",
    )
    ap.add_argument(
        "--summary-json",
        help="Write per-batch result JSON to this path on completion",
    )
    args = ap.parse_args()

    source = Path(args.source).expanduser().resolve()
    if not source.is_dir():
        print(f"[fatal] source not a directory: {source}")
        return 2

    if not Path(args.ov_bin).exists() and not args.dry_run:
        print(f"[fatal] ov binary not found at {args.ov_bin}")
        return 2

    # Throwaway ovcli.conf pointing at --port
    tmpdir = Path(tempfile.mkdtemp(prefix="ov_batch_"))
    cli_conf = tmpdir / "ovcli.conf"
    cli_conf.write_text(json.dumps({
        "url": f"http://127.0.0.1:{args.port}",
        "api_key": args.api_key,
        "timeout": 1800.0,
    }))
    ov_env = {**os.environ, "OPENVIKING_CLI_CONFIG_FILE": str(cli_conf)}

    units = list_batches(source, args.depth)
    if not units:
        print(f"[info] no units found under {source} at depth={args.depth}")
        return 0

    print(f"[plan] source       = {source}")
    print(f"[plan] parent       = {args.parent}")
    print(f"[plan] units        = {len(units)} (depth={args.depth})")
    print(f"[plan] max-size     = {human(args.max_size)}")
    print(f"[plan] skip-existing= {args.skip_existing}")
    print(f"[plan] dry-run      = {args.dry_run}")
    print("-" * 72)

    plan = []
    total_size = 0
    for u in units:
        size = du_bytes(u)
        target = f"{args.parent.rstrip('/')}/{sanitize_segment(u.name)}"
        oversized = size > args.max_size
        plan.append({
            "source": str(u),
            "target": target,
            "size": size,
            "oversized": oversized,
            "status": "pending",
            "rc": None,
            "elapsed_sec": None,
            "note": "",
        })
        total_size += size
        flag = "  SKIP-OVERSIZE" if oversized else ""
        print(f"  {human(size):>10}  {u.name}  →  {target}{flag}")
    print("-" * 72)
    print(f"[plan] total size   = {human(total_size)}")

    if args.dry_run:
        if args.summary_json:
            Path(args.summary_json).write_text(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0

    t_run_start = time.time()
    ok = fail = skipped = oversize_skip = 0

    for i, item in enumerate(plan, 1):
        src = Path(item["source"])
        target = item["target"]
        size = item["size"]

        if item["oversized"]:
            item["status"] = "skipped-oversize"
            item["note"] = f"size {human(size)} > max {human(args.max_size)}; rerun with --depth {args.depth + 1}"
            oversize_skip += 1
            print(f"[{i}/{len(plan)}] SKIP (oversize {human(size)}): {src.name}")
            continue

        if args.skip_existing and target_exists(args.ov_bin, ov_env, target, args.account, args.user):
            item["status"] = "skipped-exists"
            item["note"] = "target already exists"
            skipped += 1
            print(f"[{i}/{len(plan)}] SKIP (exists): {target}")
            continue

        cmd = [
            args.ov_bin, "add-resource", str(src),
            "--to", target,
            "--wait", "--no-progress",
            "--timeout", str(args.per_batch_timeout),
        ]
        if args.account:
            cmd += ["--account", args.account]
        if args.user:
            cmd += ["--user", args.user]

        t0 = time.time()
        print(f"[{i}/{len(plan)}] IMPORT ({human(size)}): {src.name}  →  {target}")
        rc, stdout, stderr = run_cmd(cmd, ov_env, args.per_batch_timeout + 60)
        elapsed = time.time() - t0
        item["rc"] = rc
        item["elapsed_sec"] = round(elapsed, 1)

        if rc == 0:
            item["status"] = "ok"
            ok += 1
            print(f"          ok in {elapsed:.1f}s")
        else:
            item["status"] = "failed"
            tail_err = (stderr or stdout).strip().splitlines()[-3:]
            item["note"] = " | ".join(tail_err)[:500]
            fail += 1
            print(f"          FAILED rc={rc} in {elapsed:.1f}s")
            for line in tail_err:
                print(f"            stderr> {line}")

    elapsed_total = time.time() - t_run_start
    print("=" * 72)
    print(f"[done] total={len(plan)} ok={ok} failed={fail} "
          f"skipped-exists={skipped} skipped-oversize={oversize_skip}")
    print(f"[done] elapsed {elapsed_total:.1f}s")
    if oversize_skip:
        print(f"[done] {oversize_skip} oversized batch(es) skipped — rerun with --depth {args.depth + 1}")

    if args.summary_json:
        Path(args.summary_json).write_text(json.dumps(plan, indent=2, ensure_ascii=False))
        print(f"[done] wrote summary to {args.summary_json}")

    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
