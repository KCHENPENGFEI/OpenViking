"""Unit tests for the flat URI builder."""

from scripts._flatten_uri import build_flat_uri


def test_uri_with_volume():
    assert (
        build_flat_uri("仙逆", "第一卷 平庸少年", "第一章 离乡", 1)
        == "viking://resource/仙逆_第一卷 平庸少年_第一章 离乡_1.md"
    )


def test_uri_without_volume():
    assert (
        build_flat_uri("神雕侠侣", "", "第一回 风月无情", 1)
        == "viking://resource/神雕侠侣_第一回 风月无情_1.md"
    )


def test_uri_volume_only_label():
    """《诛仙》's volumes are labelled '卷一/卷二' with no separate name."""
    assert (
        build_flat_uri("诛仙", "卷一", "第一章 青云", 2)
        == "viking://resource/诛仙_卷一_第一章 青云_2.md"
    )


def test_uri_normalizes_slashes_in_titles():
    """Defensive: slashes inside a title would break the AGFS URI; we replace them."""
    uri = build_flat_uri("X", "", "a/b", 1)
    payload = uri.removeprefix("viking://resource/")
    assert "/" not in payload, f"unexpected slash in payload: {payload!r}"
