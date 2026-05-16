"""Flat AGFS URI builder for the ov_flattern experiment.

URIs are produced under viking://resource/, with the hierarchy
(novel / volume? / chapter / chunk_idx) encoded directly into the file name
because the experiment removes the directory tree that current OpenViking
relies on for hierarchy.
"""

import re

_BAD_PATH_CHARS = re.compile(r"[/\\]")


def _sanitize(text: str) -> str:
    """Replace path separators in user-supplied strings so they cannot break the URI."""
    return _BAD_PATH_CHARS.sub("_", text).strip()


def build_flat_uri(novel: str, volume: str, chapter: str, chunk_idx: int) -> str:
    """Build a flat ``viking://resource/{novel}[_{volume}]_{chapter}_{idx}.md`` URI.

    Args:
        novel: novel name, e.g. ``"神雕侠侣"``.
        volume: volume label or name. Empty string when the novel has no volume tier.
        chapter: chapter title (heading text, e.g. ``"第一回 风月无情"``).
        chunk_idx: 1-based chunk index within the chapter.
    """
    parts = [_sanitize(novel)]
    if volume:
        parts.append(_sanitize(volume))
    parts.append(_sanitize(chapter))
    name = "_".join(parts) + f"_{chunk_idx}.md"
    return f"viking://resource/{name}"
