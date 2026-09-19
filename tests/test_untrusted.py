from __future__ import annotations

import pytest

from cleanrr.tools._untrusted import (
    bound_message_list,
    bound_text,
    quote_upstream,
    render_upstream_block,
)

# ---------------------------------------------------------------------------
# bound_text
# ---------------------------------------------------------------------------


def test_bound_text_collapses_control_chars_and_whitespace() -> None:
    assert bound_text("no files\nfound \x07are  eligible") == "no files found are eligible"


# Right-to-left override, zero-width space, no-break space, line separator,
# next line, ideographic space — none of these are ASCII-visible in source,
# so build them from codepoints rather than embedding literal characters.
_NON_PRINTABLE_CODEPOINTS = [0x202E, 0x200B, 0x00A0, 0x2028, 0x0085, 0x3000]


@pytest.mark.parametrize("codepoint", _NON_PRINTABLE_CODEPOINTS, ids=hex)
def test_bound_text_replaces_non_printable_unicode(codepoint: int) -> None:
    ch = chr(codepoint)
    result = bound_text(f"before{ch}after")
    assert ch not in result
    assert "before" in result
    assert "after" in result


def test_bound_text_keeps_printable_unicode() -> None:
    # CJK, emoji, em dash, curly quotes.
    value = "中 emoji \U0001f3ac em—dash “curly”"
    assert bound_text(value) == value


def test_bound_text_returns_exactly_limit_chars() -> None:
    result = bound_text("a" * 200, limit=120)
    assert result == "a" * 120
    assert len(result) == 120


@pytest.mark.parametrize("value", [None, 42, True, {}, [], "   ", "\n\t"])
def test_bound_text_returns_default_for_non_string_and_blank(value: object) -> None:
    assert bound_text(value) == ""
    assert bound_text(value, default="fallback") == "fallback"


def test_bound_text_strips_leading_and_trailing_whitespace() -> None:
    assert bound_text("  hello world  ") == "hello world"


def test_bound_text_slices_before_scanning_a_hostile_input() -> None:
    value = "a" * 10 + "\x00" * 100_000 + "b" * 100
    result = bound_text(value, limit=80)
    assert len(result) <= 80
    assert result.startswith("aaaaaaaaaa")
    assert "b" not in result


# ---------------------------------------------------------------------------
# bound_message_list
# ---------------------------------------------------------------------------


def test_bound_message_list_non_list_returns_empty() -> None:
    assert bound_message_list(None, limit=80, max_items=3) == []
    assert bound_message_list("not a list", limit=80, max_items=3) == []


def test_bound_message_list_caps_at_max_items() -> None:
    values = ["one", "two", "three", "four"]
    result = bound_message_list(values, limit=80, max_items=2)
    assert result == ["one", "two"]


def test_bound_message_list_drops_empty_and_duplicate_entries() -> None:
    values = ["dup", "   ", "dup", "unique"]
    result = bound_message_list(values, limit=80, max_items=5)
    assert result == ["dup", "unique"]


def test_bound_message_list_drops_non_str_entries() -> None:
    values = [None, 42, {"a": 1}, "kept"]
    result = bound_message_list(values, limit=80, max_items=5)
    assert result == ["kept"]


def test_bound_message_list_bounds_each_entry() -> None:
    values = ["a" * 200]
    result = bound_message_list(values, limit=10, max_items=5)
    assert result == ["a" * 10]


# ---------------------------------------------------------------------------
# quote_upstream
# ---------------------------------------------------------------------------


def test_quote_upstream_replaces_inner_quotes_and_wraps() -> None:
    assert quote_upstream('he said "hi"') == "\"he said 'hi'\""


# ---------------------------------------------------------------------------
# render_upstream_block
# ---------------------------------------------------------------------------


def test_render_upstream_block_empty_list_returns_empty_string() -> None:
    assert render_upstream_block("Radarr", []) == ""


def test_render_upstream_block_names_the_source_in_header() -> None:
    result = render_upstream_block("Radarr", ["a message"])
    assert "Radarr reports" in result


def test_render_upstream_block_newline_cannot_add_a_line() -> None:
    messages = bound_message_list(["a\nb\nc", "d"], limit=80, max_items=3)
    result = render_upstream_block("Radarr", messages)
    assert len(result.splitlines()) == 3
