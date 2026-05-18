"""Unit tests for the flat URI builder."""

from scripts._flatten_uri import build_flat_uri


def test_uri_with_volume():
    """Volume with internal space gets collapsed to underscore."""
    assert (
        build_flat_uri("仙逆", "第一卷 平庸少年", "第一章 离乡", 1)
        == "viking://resources/仙逆_第一卷_平庸少年_第一章_离乡_1.md"
    )


def test_uri_without_volume():
    assert (
        build_flat_uri("神雕侠侣", "", "第一回 风月无情", 1)
        == "viking://resources/神雕侠侣_第一回_风月无情_1.md"
    )


def test_uri_volume_only_label():
    """《诛仙》's volumes are labelled '卷一/卷二' with no separate name."""
    assert (
        build_flat_uri("诛仙", "卷一", "第一章 青云", 2)
        == "viking://resources/诛仙_卷一_第一章_青云_2.md"
    )


def test_uri_normalizes_slashes_in_titles():
    """Defensive: slashes inside a title would break the AGFS URI; we replace them."""
    uri = build_flat_uri("X", "", "a/b", 1)
    payload = uri.removeprefix("viking://resources/")
    assert "/" not in payload, f"unexpected slash in payload: {payload!r}"


def test_uri_trims_field_whitespace():
    """Leading/trailing whitespace on each field is trimmed before joining."""
    assert build_flat_uri("  X  ", " V ", "  第一章  ", 1) == "viking://resources/X_V_第一章_1.md"


def test_uri_collapses_consecutive_whitespace():
    """Multiple consecutive spaces collapse into a single underscore."""
    assert build_flat_uri("X", "", "a    b", 1) == "viking://resources/X_a_b_1.md"


def test_uri_sanitize_is_idempotent():
    """Sanitizing an already-sanitized field is a no-op."""
    from scripts._flatten_uri import _sanitize

    once = _sanitize("a  b  c")
    twice = _sanitize(once)
    assert once == twice == "a_b_c"
