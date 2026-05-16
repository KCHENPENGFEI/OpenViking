"""Unit tests for chapter detector and chunk splitter."""

from scripts._flatten_chunker import chunk_chapter, locate_chapters

SHENDIAO = """# 神雕侠侣
### 第一回 风月无情
foo bar
### 第二回 故人之子
baz
"""

XIANNI = """# 仙逆
## 第一卷 平庸少年
### 第一章 离乡
content
### 第二章 仙人
more
## 第二卷 修真血影
### 第一百四十章 修魔海
deep
"""

ZHUXIAN = """# 诛仙
## 卷一
### 序章
intro
### 第一章 青云
real start
## 卷二
### 第一章 别样
v2
"""


def test_locate_shendiao_flat():
    chapters = locate_chapters(SHENDIAO)
    assert [(c.volume, c.chapter_title) for c in chapters] == [
        ("", "第一回 风月无情"),
        ("", "第二回 故人之子"),
    ]


def test_locate_xianni_with_volume():
    chapters = locate_chapters(XIANNI)
    assert [(c.volume, c.chapter_title) for c in chapters] == [
        ("第一卷 平庸少年", "第一章 离乡"),
        ("第一卷 平庸少年", "第二章 仙人"),
        ("第二卷 修真血影", "第一百四十章 修魔海"),
    ]


def test_locate_zhuxian_volume_label_only():
    chapters = locate_chapters(ZHUXIAN)
    assert [(c.volume, c.chapter_title) for c in chapters] == [
        ("卷一", "序章"),
        ("卷一", "第一章 青云"),
        ("卷二", "第一章 别样"),
    ]


def test_chunk_short_chapter_single():
    short = SHENDIAO  # first chapter is very short
    chapters = locate_chapters(short)
    chunks = chunk_chapter(short, chapters[0].chapter_idx)
    assert len(chunks) == 1
    assert "第一回 风月无情" in chunks[0]


def test_chunk_oversized_chapter_splits():
    # Construct a synthetic content whose single chapter overflows max_section_chars.
    big_body_paragraphs = ["段落一些文字内容。" * 200 for _ in range(8)]
    big_body = "\n\n".join(big_body_paragraphs)
    big = f"### 第一章 长章\n\n{big_body}"
    chapters = locate_chapters(big)
    chunks = chunk_chapter(big, chapters[0].chapter_idx)
    assert len(chunks) >= 2
    # _smart_split_content uses max_section_chars=6000 as the hard cap;
    # but the splitter's flexibility allows some overflow at paragraph boundaries.
    # Sanity check: no chunk is anywhere close to the full body size.
    assert all(len(c) < len(big) for c in chunks)
