"""Minimal reproducer: 1000-entry ceiling on add_resource.

Drives the running OpenViking server through the `ov` CLI (so we reuse
the CLI's auth/multipart/identity wiring). Generates a single markdown
file with N H1-only chapters, imports it via `ov add-resource --wait`,
then compares parser log output against ground-truth on-disk entries.

Usage:
    # Default 2200 chapters, 800 chars each
    python scripts/repro_markdown_chapter_loss.py

    # Smaller smoke test (should NOT hit the ceiling)
    python scripts/repro_markdown_chapter_loss.py --chapters 800

    # Custom workspace / parent / account
    python scripts/repro_markdown_chapter_loss.py \\
        --workspace ~/.openviking/data-novel \\
        --parent viking://resources \\
        --target-name repro_md_lost \\
        --account qijing_ai_doubao_pro \\
        --user doubao_pro

What the verdict means:
    OK                              - chapters match disk entries (no ceiling hit)
    1000-ENTRY CEILING REPRODUCED   - disk visible entries == 1000 while
                                       parser claims more chapters Saved
    MISMATCH                        - other kind of loss (race? truncation?)

After the run, the script also checks how many [write_verify] ERROR lines
landed in the log during this run window — this tells you whether script-2
(`OV_WRITE_VERIFY=1` env on the server) is wired up and whether any writes
came back 200 but failed to persist.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def build_markdown(n_chapters: int, chars_per_chapter: int) -> str:
    filler_unit = "凡所有相皆是虚妄若见诸相非相即见如来"
    repeat = (chars_per_chapter // len(filler_unit)) + 1
    filler = (filler_unit * repeat)[:chars_per_chapter]
    parts = []
    for i in range(1, n_chapters + 1):
        parts.append(f"# 第{i:05d}章 测试标题_{i}\n\n{filler}\n")
    return "\n".join(parts)


def run_ov_add_resource(
    ov_bin: str, src_path: Path, to_uri: str, account: str | None,
    user: str | None, extra: list[str], env: dict | None = None,
) -> int:
    cmd = [
        ov_bin, "add-resource", str(src_path),
        "--to", to_uri,
        "--wait",
        "--no-progress",
        "--timeout", "1200",
    ]
    if account:
        cmd += ["--account", account]
    if user:
        cmd += ["--user", user]
    cmd += extra
    print(f"[ov] {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1500, env=env)
    if proc.stdout:
        print("[ov stdout]", proc.stdout.strip().splitlines()[-5:])
    if proc.stderr:
        print("[ov stderr]", proc.stderr.strip().splitlines()[-5:])
    return proc.returncode


def run_ov_rm(
    ov_bin: str, to_uri: str, account: str | None, user: str | None,
    env: dict | None = None,
) -> None:
    # CLI exposes deletion via `ov rm` — best-effort
    cmd = [ov_bin, "rm", to_uri, "--recursive"]
    if account:
        cmd += ["--account", account]
    if user:
        cmd += ["--user", user]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
    except Exception:
        pass


_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d{3})")


def _parse_ts(line: str) -> float | None:
    m = _TS_RE.match(line)
    if not m:
        return None
    return time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")) + int(m.group(2)) / 1000


def tally_log(log_path: Path, t_start: float, t_end: float) -> dict:
    """Tally interesting markers in the log within [t_start, t_end]."""
    out = {
        "saved_total": 0,
        "saved_unique": 0,
        "write_verify_errors": 0,
        "split_calls": 0,
        "merged_calls": 0,
        "found_headings_lines": [],
    }
    if not log_path.is_file():
        print(f"[log] not found: {log_path}")
        return out
    seen_names = set()
    pad = 1.0  # 1s grace on each side for clock skew
    for line in log_path.read_text(errors="replace").splitlines():
        ts = _parse_ts(line)
        if ts is None or ts < t_start - pad or ts > t_end + pad:
            continue
        if "[MarkdownParser] Saved:" in line:
            out["saved_total"] += 1
            name = line.split("Saved:", 1)[1].strip()
            seen_names.add(name)
        if "[MarkdownParser] Splitting:" in line:
            out["split_calls"] += 1
        if "[MarkdownParser] Merged" in line:
            out["merged_calls"] += 1
        if "[write_verify]" in line:
            out["write_verify_errors"] += 1
        if "Found" in line and "headings" in line:
            out["found_headings_lines"].append(line.strip())
    out["saved_unique"] = len(seen_names)
    return out


def find_target_on_disk(workspace: Path, parent_uri: str, name: str) -> Path | None:
    """Locate the on-disk dir for parent_uri/name across accounts."""
    body = parent_uri.split("://", 1)[-1].rstrip("/") + "/" + name
    viking_root = workspace / "viking"
    if not viking_root.is_dir():
        return None
    candidates = []
    for acct in viking_root.iterdir():
        cand = acct / body
        if cand.is_dir():
            candidates.append(cand)
    if not candidates:
        return None
    return max(candidates, key=lambda p: sum(1 for _ in p.iterdir()))


def count_disk_entries(target_dir: Path) -> tuple[int, int]:
    files = 0
    dirs = 0
    for p in target_dir.iterdir():
        if p.name.startswith("."):
            continue
        if p.is_file():
            files += 1
        elif p.is_dir():
            dirs += 1
    return files, files + dirs


def count_disk_md_recursive(target_dir: Path) -> int:
    """Total non-hidden .md files anywhere under target_dir."""
    total = 0
    for p in target_dir.rglob("*.md"):
        if any(seg.startswith(".") for seg in p.relative_to(target_dir).parts):
            continue
        total += 1
    return total


def per_book_summary(target_dir: Path) -> list[tuple[str, int, int]]:
    """For each direct subdir of target_dir, return (name, top-level .md count, recursive .md count)."""
    out = []
    for sub in sorted(target_dir.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        top = sum(
            1 for p in sub.iterdir()
            if p.is_file() and p.suffix == ".md" and not p.name.startswith(".")
        )
        rec = count_disk_md_recursive(sub)
        out.append((sub.name, top, rec))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapters", type=int, default=2200)
    ap.add_argument("--chars", type=int, default=800)
    ap.add_argument(
        "--num-files", type=int, default=1,
        help="N markdown files in one upload (simulates directory parser concurrency). "
             "Each file gets `chapters` H1 sections. Default 1 = single-file flow.",
    )
    ap.add_argument("--target-name", default="repro_md_lost")
    ap.add_argument("--parent", default="viking://resources")
    ap.add_argument("--workspace", default=os.path.expanduser("~/.openviking/data-novel"))
    ap.add_argument(
        "--ov-bin",
        default=str(
            Path(__file__).resolve().parent.parent / ".venv-flattern/bin/ov"
        ),
    )
    ap.add_argument("--account", default="qijing_ai_doubao_pro")
    ap.add_argument("--user", default="doubao_pro")
    ap.add_argument(
        "--port", type=int, default=1935,
        help="OpenViking server port (matches ov-novel.conf, default 1935)",
    )
    ap.add_argument(
        "--api-key",
        default=os.environ.get(
            "OPENVIKING_API_KEY",
            "cWlqaW5nX2FpX2RvdWJhb19wcm8.ZG91YmFvX3Bybw.ZDVmZDg0ZTdiZmQ3ZWM1MzQwNGJhMDI1YmQ1NDZiMDIyYWZjZDNjNzkwN2ZjMDRkMjVmODJkNmUyNDEyMzBjMw",
        ),
        help="API key for the target server (defaults to existing ovcli.conf key)",
    )
    ap.add_argument("--keep", action="store_true")
    ap.add_argument(
        "--extra", nargs=argparse.REMAINDER,
        help="extra args passed through to `ov add-resource`",
    )
    args = ap.parse_args()

    workspace = Path(args.workspace).expanduser()
    log_path = workspace / "log" / "openviking.log"
    target_uri = f"{args.parent.rstrip('/')}/{args.target_name}"

    if not Path(args.ov_bin).exists():
        print(f"[fatal] ov binary not found at {args.ov_bin}")
        return 2

    # Build a throwaway ovcli.conf pointing at --port so `ov` subprocess
    # talks to the right server without touching the user's real config.
    tmpdir = Path(tempfile.mkdtemp(prefix="ov_repro_"))
    cli_conf = tmpdir / "ovcli.conf"
    cli_conf.write_text(json.dumps({
        "url": f"http://127.0.0.1:{args.port}",
        "api_key": args.api_key,
        "timeout": 1800.0,
    }))
    ov_env = {**os.environ, "OPENVIKING_CLI_CONFIG_FILE": str(cli_conf)}

    print(f"[setup] ov={args.ov_bin}")
    print(f"[setup] server=http://127.0.0.1:{args.port}  (via tmp {cli_conf})")
    print(f"[setup] target={target_uri}")
    print(f"[setup] workspace={workspace}")
    print(f"[setup] chapters={args.chapters} chars/chapter={args.chars} num_files={args.num_files}")

    # 0. Pre-clean target
    run_ov_rm(args.ov_bin, target_uri, args.account, args.user, env=ov_env)

    # 1. Build markdown source. Single-file when num_files=1 keeps original
    # behavior; multi-file dumps N uniquely-named .md into a source dir,
    # then add-resource imports the directory (exercises directory parser).
    if args.num_files <= 1:
        md = build_markdown(args.chapters, args.chars)
        print(f"[build] md size = {len(md)} chars, {len(md.encode())} bytes")
        src_path = tmpdir / f"{args.target_name}.md"
        src_path.write_text(md, encoding="utf-8")
        print(f"[build] wrote single file {src_path}")
    else:
        src_path = tmpdir / "source"
        src_path.mkdir()
        for i in range(1, args.num_files + 1):
            md = build_markdown(args.chapters, args.chars)
            # Make each book's H1 titles unique across files to dodge
            # any cross-file sanitize collisions; chapter index already
            # uniquifies within a single file via build_markdown.
            md = md.replace("测试标题_", f"book{i:02d}标题_")
            (src_path / f"book_{i:02d}.md").write_text(md, encoding="utf-8")
        total_h1 = args.chapters * args.num_files
        print(
            f"[build] wrote {args.num_files} files under {src_path} "
            f"(total H1 = {total_h1})"
        )

    # 2. Run ov add-resource
    t_start = time.time()
    rc = run_ov_add_resource(
        args.ov_bin, src_path, target_uri, args.account, args.user,
        args.extra or [], env=ov_env,
    )
    t_end = time.time()
    print(f"[time] parse window: {t_start:.3f} .. {t_end:.3f} ({t_end - t_start:.1f}s)")
    if rc != 0:
        print(f"[warn] ov returned {rc}, continuing to tally anyway")

    # 3. Tally
    log_stats = tally_log(log_path, t_start, t_end)
    target_dir = find_target_on_disk(workspace, args.parent, args.target_name)
    expected_chapters = args.chapters * max(args.num_files, 1)
    if target_dir is None:
        print(f"[disk] no on-disk dir found under {workspace}/viking/*/{args.parent.split('://')[-1]}/{args.target_name}")
        files = visible = recursive_md = -1
        per_book = []
    else:
        print(f"[disk] scanning {target_dir}")
        files, visible = count_disk_entries(target_dir)
        recursive_md = count_disk_md_recursive(target_dir)
        per_book = per_book_summary(target_dir) if args.num_files > 1 else []

    print("=" * 60)
    for line in log_stats["found_headings_lines"][:5]:
        print(f"[log] {line}")
    print(f"Input chapters (H1)          : {args.chapters} x {args.num_files} = {expected_chapters}")
    print(f"Parser Saved log lines       : {log_stats['saved_total']}")
    print(f"Parser Saved unique filenames: {log_stats['saved_unique']}")
    print(f"Parser Splitting calls       : {log_stats['split_calls']}")
    print(f"Parser Merged calls          : {log_stats['merged_calls']}")
    print(f"Disk .md (depth=1)           : {files}")
    print(f"Disk .md (recursive)         : {recursive_md}")
    print(f"Disk visible entries (f+d)   : {visible}")
    if per_book:
        print("Per-book breakdown (sub, top-level .md, recursive .md):")
        for name, top, rec in per_book:
            print(f"  {name:30s} top={top:6d} rec={rec:6d}")
    print(f"[write_verify] ERROR lines   : {log_stats['write_verify_errors']}")
    if log_stats["write_verify_errors"] == 0:
        print("                                (0 = either no silent drop OR")
        print("                                 OV_WRITE_VERIFY=1 not set on server)")
    print("=" * 60)

    verdict = "OK"
    if recursive_md != -1 and recursive_md < expected_chapters:
        gap = expected_chapters - recursive_md
        verdict = f"MISMATCH: {gap} chapters missing on disk (expected {expected_chapters}, got {recursive_md})"
    elif visible == 1000 and expected_chapters > 1000 and args.num_files <= 1:
        verdict = "1000-ENTRY CEILING REPRODUCED"
    print(f"VERDICT: {verdict}")

    # 4. Cleanup
    if not args.keep:
        run_ov_rm(args.ov_bin, target_uri, args.account, args.user, env=ov_env)
        print(f"[cleanup] removed {target_uri}")
    shutil.rmtree(tmpdir, ignore_errors=True)

    return 0 if verdict == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
