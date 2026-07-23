from __future__ import annotations

from cleanrr.tools._user_request import UserRequestLookup, render_lookup_error


def _lookup(status: str, candidates: list | None = None) -> UserRequestLookup:
    return UserRequestLookup(status=status, candidates=candidates)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ok → None (caller handles the happy path)
# ---------------------------------------------------------------------------


def test_render_ok_returns_none() -> None:
    assert render_lookup_error(_lookup("ok"), "Dune") is None


# ---------------------------------------------------------------------------
# Each non-ok status returns the expected text
# ---------------------------------------------------------------------------


def test_render_not_configured() -> None:
    result = render_lookup_error(_lookup("not_configured"), "Dune")
    assert result is not None
    assert result["is_error"] is True
    assert "Overseerr isn't configured" in result["content"][0]["text"]


def test_render_context_missing() -> None:
    result = render_lookup_error(_lookup("context_missing"), "Dune")
    assert result is not None
    assert result["is_error"] is True
    assert "Internal error" in result["content"][0]["text"]


def test_render_unlinked_user() -> None:
    result = render_lookup_error(_lookup("unlinked_user"), "Dune")
    assert result is not None
    assert result["is_error"] is False
    assert "/link" in result["content"][0]["text"]


def test_render_empty_input() -> None:
    result = render_lookup_error(_lookup("empty_input"), "")
    assert result is not None
    assert result["is_error"] is False
    assert "which title" in result["content"][0]["text"]


def test_render_user_not_found() -> None:
    result = render_lookup_error(_lookup("user_not_found"), "Dune")
    assert result is not None
    assert result["is_error"] is False
    assert "Couldn't find your Overseerr account" in result["content"][0]["text"]


def test_render_parse_error() -> None:
    result = render_lookup_error(_lookup("parse_error"), "Dune")
    assert result is not None
    assert result["is_error"] is True
    assert "Unexpected response format" in result["content"][0]["text"]


def test_render_http_error() -> None:
    result = render_lookup_error(_lookup("http_error"), "Dune")
    assert result is not None
    assert result["is_error"] is True
    assert "Couldn't reach Overseerr" in result["content"][0]["text"]


def test_render_no_match_includes_title() -> None:
    result = render_lookup_error(_lookup("no_match"), "My Movie")
    assert result is not None
    assert result["is_error"] is False
    assert "My Movie" in result["content"][0]["text"]
    assert "list your requests" in result["content"][0]["text"]


def test_render_no_match_truncates_long_title() -> None:
    long_title = "A" * 60
    result = render_lookup_error(_lookup("no_match"), long_title)
    assert result is not None
    text = result["content"][0]["text"]
    assert "A" * 50 in text
    assert "A" * 51 not in text


def test_render_multi_match_with_candidates() -> None:
    candidates = [
        {"media": {"title": "Dune Part One", "releaseYear": 2021}},
        {"media": {"title": "Dune Part Two", "releaseYear": 2024}},
    ]
    result = render_lookup_error(_lookup("multi_match", candidates=candidates), "Dune")
    assert result is not None
    assert result["is_error"] is False
    text = result["content"][0]["text"]
    assert "2 possible matches" in text
    assert "Dune Part One (2021)" in text
    assert "Dune Part Two (2024)" in text


def test_render_multi_match_candidate_without_year() -> None:
    candidates = [{"media": {"title": "Dune"}}]
    result = render_lookup_error(_lookup("multi_match", candidates=candidates), "Dune")
    assert result is not None
    text = result["content"][0]["text"]
    assert "- Dune\n" in text or text.endswith("- Dune")


def test_render_multi_match_candidate_uses_name_fallback() -> None:
    candidates = [{"media": {"name": "The Bear", "releaseYear": 2022}}]
    result = render_lookup_error(_lookup("multi_match", candidates=candidates), "bear")
    assert result is not None
    assert "The Bear (2022)" in result["content"][0]["text"]


def test_render_multi_match_none_candidates_returns_error() -> None:
    result = render_lookup_error(_lookup("multi_match", candidates=None), "Dune")
    assert result is not None
    assert result["is_error"] is True
    assert "error occurred" in result["content"][0]["text"]


def test_render_unknown_status_returns_none() -> None:
    # Any status not in the Literal set should fall through to None
    result = render_lookup_error(_lookup("totally_unknown"), "Dune")
    assert result is None
