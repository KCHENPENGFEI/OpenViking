"""Count Chinese characters in markdown novel files.

Counts only CJK characters per file (the conventional "字数" definition for
pure-Chinese novels) — strips a few common markdown bits (fenced code,
headers, blockquote markers, list bullets) to avoid double-counting syntax.
ASCII letters and digits are excluded.

Usage:
    python scripts/novel_eval/count_words.py path/to/novel.md ...
    python scripts/novel_eval/count_words.py path/to/novels_dir/

Pass --csv to emit machine-readable output.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

# CJK Unified Ideographs + Extension A. Covers the vast majority of
# characters in Chinese novels. (Rare extensions B-F are ignored.)
_CJK_RE = re.compile(r"[一-鿿㐀-䶿]")

_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")


def _strip_markdown_noise(text: str) -> str:
    """Remove a few markdown bits that don't carry "字数"."""
    text = _FENCED_CODE_RE.sub("", text)
    text = _INLINE_CODE_RE.sub("", text)
    cleaned_lines = []
    for line in text.splitlines():
        stripped = line.lstrip()
        # Skip markdown structural prefixes; everything after them is still text
        # but the markers themselves shouldn't influence count (they're ASCII anyway,
        # so this is mostly belt-and-suspenders).
        if stripped.startswith("```"):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def count_cjk(text: str) -> int:
    return len(_CJK_RE.findall(text))


def _iter_md_files(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    for p in paths:
        if p.is_file():
            if p.suffix.lower() == ".md":
                found.append(p)
            else:
                print(f"warn: {p} is not .md, skipping", file=sys.stderr)
        elif p.is_dir():
            for f in sorted(p.rglob("*.md")):
                found.append(f)
        else:
            print(f"warn: {p} not found, skipping", file=sys.stderr)
    return found


def count_file(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    cleaned = _strip_markdown_noise(text)
    return count_cjk(cleaned)


def main() -> int:
    ap = argparse.ArgumentParser(description="Count CJK characters in markdown novels.")
    ap.add_argument("paths", nargs="+", type=Path, help=".md files or dirs (rglob *.md)")
    ap.add_argument("--csv", action="store_true", help="Emit CSV (path,cjk_chars) to stdout")
    args = ap.parse_args()

    files = _iter_md_files(args.paths)
    if not files:
        print("no .md files found", file=sys.stderr)
        return 1

    counts: list[tuple[Path, int]] = []
    for f in files:
        try:
            n = count_file(f)
        except Exception as e:
            print(f"error reading {f}: {e}", file=sys.stderr)
            continue
        counts.append((f, n))

    total = sum(n for _, n in counts)

    if args.csv:
        w = csv.writer(sys.stdout)
        w.writerow(["path", "cjk_chars"])
        for f, n in counts:
            w.writerow([str(f), n])
        w.writerow(["__TOTAL__", total])
    else:
        width = max((len(str(f)) for f, _ in counts), default=20)
        for f, n in counts:
            print(f"{str(f):<{width}}  {n:>10,}")
        print("-" * (width + 13))
        print(f"{'TOTAL':<{width}}  {total:>10,}  ({len(counts)} files)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
