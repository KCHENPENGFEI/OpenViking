"""Flat AGFS URI builder for the ov_flattern experiment.

URIs are produced under viking://resources/, with the hierarchy
(novel / volume? / chapter / chunk_idx) encoded directly into the file name
because the experiment removes the directory tree that current OpenViking
relies on for hierarchy.
"""

import re

# Path separators + any whitespace (incl. CJK full-width space 　, tab, etc.)
# get collapsed into a single underscore.
_SANITIZE_PATTERN = re.compile(r"[\s/\\]+")


def _sanitize(text: str) -> str:
    """Normalize a URI field: trim both ends, then collapse whitespace/path
    separators into a single underscore. Idempotent.
    """
    return _SANITIZE_PATTERN.sub("_", text.strip())


def build_flat_uri(novel: str, volume: str, chapter: str, chunk_idx: int) -> str:
    """Build a flat ``viking://resources/{novel}[_{volume}]_{chapter}_{idx}.md`` URI.

    Each field passes through ``_sanitize`` first.
    """
    parts = [_sanitize(novel)]
    if volume:
        sanitized_volume = _sanitize(volume)
        if sanitized_volume:
            parts.append(sanitized_volume)
    parts.append(_sanitize(chapter))
    name = "_".join(parts) + f"_{chunk_idx}.md"
    return f"viking://resources/{name}"
