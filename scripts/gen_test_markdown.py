"""Generate a synthetic markdown file with N H1-only chapters for OpenViking ingestion tests.

Each chapter's body length is randomly drawn from [--min-chars, --max-chars].

Usage:
    python scripts/gen_test_markdown.py --chapters 2200 --min-chars 600 --max-chars 1200 -o /tmp/test.md
    python scripts/gen_test_markdown.py --chapters 3930 --min-chars 800 --max-chars 2000 -o ~/Downloads/repro_big.md
    # Reproducible:
    python scripts/gen_test_markdown.py --chapters 100 --min-chars 500 --max-chars 1000 --seed 42 -o /tmp/test.md
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path


FILLER_UNIT = "凡所有相皆是虚妄若见诸相非相即见如来"


def make_filler(n_chars: int) -> str:
    repeat = (n_chars // len(FILLER_UNIT)) + 1
    return (FILLER_UNIT * repeat)[:n_chars]


def build_markdown(
    n_chapters: int, min_chars: int, max_chars: int, rng: random.Random,
) -> tuple[str, list[int]]:
    """Return (markdown_str, per_chapter_char_counts)."""
    parts = []
    lengths = []
    for i in range(1, n_chapters + 1):
        n = rng.randint(min_chars, max_chars)
        lengths.append(n)
        parts.append(f"# 第{i:05d}章 测试标题_{i}\n\n{make_filler(n)}\n")
    return "\n".join(parts), lengths


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--chapters", type=int, required=True, help="Number of H1 chapters")
    ap.add_argument(
        "--min-chars", type=int, required=True,
        help="Minimum filler chars per chapter (inclusive)",
    )
    ap.add_argument(
        "--max-chars", type=int, required=True,
        help="Maximum filler chars per chapter (inclusive)",
    )
    ap.add_argument(
        "--seed", type=int, default=None,
        help="Optional RNG seed for reproducible output (default: random)",
    )
    ap.add_argument(
        "-o", "--output", required=True,
        help="Output .md file path (will be overwritten)",
    )
    args = ap.parse_args()

    if args.chapters <= 0:
        print("[fatal] --chapters must be positive")
        return 2
    if args.min_chars <= 0 or args.max_chars <= 0:
        print("[fatal] --min-chars and --max-chars must be positive")
        return 2
    if args.min_chars > args.max_chars:
        print("[fatal] --min-chars must be <= --max-chars")
        return 2

    out = Path(args.output).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    rng = random.Random(args.seed)
    md, lengths = build_markdown(args.chapters, args.min_chars, args.max_chars, rng)
    out.write_text(md, encoding="utf-8")

    avg = sum(lengths) / len(lengths)
    print(f"[ok] wrote {out}")
    print(f"     chapters         = {args.chapters}")
    print(f"     chars/chap range = [{args.min_chars}, {args.max_chars}]")
    print(f"     chars/chap min   = {min(lengths)}")
    print(f"     chars/chap max   = {max(lengths)}")
    print(f"     chars/chap avg   = {avg:.1f}")
    print(f"     total chars      = {len(md)}")
    print(f"     total bytes      = {len(md.encode('utf-8'))}")
    if args.seed is not None:
        print(f"     seed             = {args.seed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
