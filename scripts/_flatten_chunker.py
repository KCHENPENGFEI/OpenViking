"""Chapter detection + chunk splitting for the ov_flattern experiment.

This module reuses internals from ``openviking.parse.parsers.markdown.MarkdownParser``
so that chunking behavior stays identical to the OpenViking baseline — the only
difference in the experiment is that we flatten everything under
``viking://resource/`` instead of building a nested directory tree.

Public API:
- ``locate_chapters(content)``: find chapter sections + their owning volume.
  Returned ``Chapter`` objects already hold sanitized ``volume`` and
  ``chapter_title`` values (whitespace trimmed and internal whitespace
  collapsed into ``_``), matching the URI builder's convention.
- ``chunk_chapter(full_content, chapter_idx_in_headings)``: produce chunks for a
  single chapter, falling back to paragraph-aware splitting when the chapter
  exceeds size thresholds. The returned chunk strings are raw markdown content
  (NOT sanitized) so heading lines and body text stay intact.
"""

from dataclasses import dataclass
from typing import List

from openviking.parse.parsers.markdown import MarkdownParser
from scripts._flatten_uri import _sanitize


@dataclass
class Chapter:
    """A single chapter located inside a novel markdown document.

    Attributes:
        volume: Owning volume label, sanitized (whitespace collapsed to ``_``).
            Empty string when the novel skips the volume tier
            (e.g. 神雕侠侣 H1→H3).
        chapter_title: Sanitized chapter title without the ``#`` markers.
        chapter_idx: Index of this chapter's heading in the list produced by
            ``MarkdownParser._find_headings(content)``. Pass this to
            ``chunk_chapter`` to chunk the chapter.
    """

    volume: str
    chapter_title: str
    chapter_idx: int


def locate_chapters(content: str) -> List[Chapter]:
    """Identify chapter sections and their owning volume label.

    Rules:
    - ``chapter_level`` is the deepest heading level present in the document.
    - ``volume_level`` is ``chapter_level - 1`` *if* that level exists in the
      document; otherwise volumes are absent (神雕侠侣 case).
    - Walking the headings in document order, any heading at ``volume_level``
      updates the current volume label; any heading at ``chapter_level`` emits
      a ``Chapter`` whose ``volume`` is the most recently seen label (or "").
    - Returned ``Chapter.volume`` and ``Chapter.chapter_title`` are sanitized
      via ``_sanitize`` (trim + whitespace→``_``) to match the URI builder.
    """
    parser = MarkdownParser()
    headings = parser._find_headings(content)
    if not headings:
        return []

    levels = {h[3] for h in headings}
    chapter_level = max(levels)
    volume_level = chapter_level - 1 if (chapter_level - 1) in levels else None

    chapters: List[Chapter] = []
    current_volume = ""
    for idx, (_start, _end, title, lvl) in enumerate(headings):
        if volume_level is not None and lvl == volume_level:
            current_volume = _sanitize(title)
            continue
        if lvl == chapter_level:
            chapters.append(
                Chapter(
                    volume=current_volume,
                    chapter_title=_sanitize(title),
                    chapter_idx=idx,
                )
            )
    return chapters


def chunk_chapter(full_content: str, chapter_idx_in_headings: int) -> List[str]:
    """Produce chunks for a single chapter.

    Reuses ``MarkdownParser._get_section_info`` to extract the chapter content
    (heading prefix + body) and ``_smart_split_content`` to paragraph-split when
    the chapter exceeds size thresholds. Behaviour matches OpenViking's
    baseline ingestion: a chapter that fits within both ``max_section_size``
    tokens and ``max_section_chars`` chars produces a single chunk; otherwise
    the chapter is split paragraph-aware with char-level fallback.

    Returned chunk strings are RAW markdown content — no sanitization is
    applied so headings like ``### 第一章 离乡`` and body text are preserved.

    Args:
        full_content: The entire novel markdown text.
        chapter_idx_in_headings: Index returned by ``locate_chapters`` for the
            chapter to chunk (i.e. ``Chapter.chapter_idx``).
    """
    parser = MarkdownParser()
    headings = parser._find_headings(full_content)
    info = parser._get_section_info(full_content, headings, chapter_idx_in_headings)
    section_text = info["content"]
    tokens = info["tokens"]

    max_size = parser.config.max_section_size
    max_chars = parser.config.max_section_chars

    if tokens <= max_size and len(section_text) <= max_chars:
        return [section_text]
    return parser._smart_split_content(section_text, max_size)
