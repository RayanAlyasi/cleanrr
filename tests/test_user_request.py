from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from cleanrr.config import Settings
from cleanrr.identity import Identity
from cleanrr.tools._context import current_telegram_user_id
from cleanrr.tools._user_request import (
    _fetch_media_title,
    _fuzzy_match_titles,
    _resolve_user_id,
    _title_match_score,
    enrich_titles_with_names,
    find_user_request,
)


def _settings(**overrides: object) -> Settings:
    base = {
        "telegram_bot_token": "test_token",
        "anthropic_api_key": "sk-test",
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def mock_identity() -> MagicMock:
    return MagicMock(spec=Identity)


@pytest.fixture
def mock_client() -> AsyncMock:
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def settings() -> Settings:
    return _settings(
        overseerr_url="http://overseerr:5055",
        overseerr_api_key="test_key",
    )


# ---------------------------------------------------------------------------
# _resolve_user_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_user_id_success(mock_client: AsyncMock) -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"results": [{"id": 42}]}
    mock_client.get.return_value = resp

    user_id, label = await _resolve_user_id(mock_client, "http://overseerr:5055", "alice")
    assert user_id == 42
    assert label == "ok"


@pytest.mark.asyncio
async def test_resolve_user_id_404_returns_user_not_found(mock_client: AsyncMock) -> None:
    resp = MagicMock()
    resp.status_code = 404
    mock_client.get.return_value = resp

    user_id, label = await _resolve_user_id(mock_client, "http://overseerr:5055", "alice")
    assert user_id is None
    assert label == "user_not_found"


@pytest.mark.asyncio
async def test_resolve_user_id_empty_results(mock_client: AsyncMock) -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"results": []}
    mock_client.get.return_value = resp

    user_id, label = await _resolve_user_id(mock_client, "http://overseerr:5055", "alice")
    assert user_id is None
    assert label == "user_not_found"


@pytest.mark.asyncio
async def test_resolve_user_id_500_returns_http_error(mock_client: AsyncMock) -> None:
    resp = MagicMock()
    resp.status_code = 500
    mock_client.get.return_value = resp

    user_id, label = await _resolve_user_id(mock_client, "http://overseerr:5055", "alice")
    assert user_id is None
    assert label == "http_error"


@pytest.mark.asyncio
async def test_resolve_user_id_malformed_json(mock_client: AsyncMock) -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("bad json")
    mock_client.get.return_value = resp

    user_id, label = await _resolve_user_id(mock_client, "http://overseerr:5055", "alice")
    assert user_id is None
    assert label == "parse_error"


@pytest.mark.asyncio
async def test_resolve_user_id_non_dict_response(mock_client: AsyncMock) -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = ["not", "a", "dict"]
    mock_client.get.return_value = resp

    user_id, label = await _resolve_user_id(mock_client, "http://overseerr:5055", "alice")
    assert user_id is None
    assert label == "parse_error"


@pytest.mark.asyncio
async def test_resolve_user_id_non_list_results(mock_client: AsyncMock) -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"results": "not-a-list"}
    mock_client.get.return_value = resp

    user_id, label = await _resolve_user_id(mock_client, "http://overseerr:5055", "alice")
    assert user_id is None
    assert label == "parse_error"


@pytest.mark.asyncio
async def test_resolve_user_id_non_dict_result_entry(mock_client: AsyncMock) -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"results": ["not-a-dict"]}
    mock_client.get.return_value = resp

    user_id, label = await _resolve_user_id(mock_client, "http://overseerr:5055", "alice")
    assert user_id is None
    assert label == "parse_error"


@pytest.mark.asyncio
async def test_resolve_user_id_picks_exact_match_from_unfiltered_results(
    mock_client: AsyncMock,
) -> None:
    """Vanilla Overseerr's /user endpoint doesn't support `q` filtering (only
    Jellyseerr does) — a non-filtering backend returns an unrelated page of
    users. Must find "alice" by exact match rather than trusting position 0."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "results": [
            {"id": 1, "username": "zx307"},
            {"id": 11, "username": "alice"},
            {"id": 5, "plexUsername": "Mum"},
        ]
    }
    mock_client.get.return_value = resp

    user_id, label = await _resolve_user_id(mock_client, "http://overseerr:5055", "alice")
    assert user_id == 11
    assert label == "ok"


@pytest.mark.asyncio
async def test_resolve_user_id_case_insensitive_match(mock_client: AsyncMock) -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"results": [{"id": 3, "jellyfinUsername": "Alice"}]}
    mock_client.get.return_value = resp

    user_id, label = await _resolve_user_id(mock_client, "http://overseerr:5055", "alice")
    assert user_id == 3
    assert label == "ok"


@pytest.mark.asyncio
async def test_resolve_user_id_ambiguous_no_match_among_multiple_is_not_found(
    mock_client: AsyncMock,
) -> None:
    """Multiple non-matching candidates with no `q` filtering in effect —
    refuse to guess rather than silently resolving to the wrong account."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "results": [
            {"id": 1, "username": "zx307"},
            {"id": 5, "username": "Mum"},
        ]
    }
    mock_client.get.return_value = resp

    user_id, label = await _resolve_user_id(mock_client, "http://overseerr:5055", "alice")
    assert user_id is None
    assert label == "user_not_found"


# ---------------------------------------------------------------------------
# find_user_request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_user_request_not_configured(mock_identity: MagicMock) -> None:
    unconfigured = _settings(overseerr_url=None, overseerr_api_key=None)
    result = await find_user_request(None, mock_identity, unconfigured, "Dune")
    assert result.status == "not_configured"


@pytest.mark.asyncio
async def test_find_user_request_context_missing(
    mock_client: AsyncMock, mock_identity: MagicMock, settings: Settings
) -> None:
    result = await find_user_request(mock_client, mock_identity, settings, "Dune")
    assert result.status == "context_missing"
    assert mock_client.get.call_count == 0


@pytest.mark.asyncio
async def test_find_user_request_unlinked_user(
    mock_client: AsyncMock, mock_identity: MagicMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value=None)

    token = current_telegram_user_id.set(1)
    try:
        result = await find_user_request(mock_client, mock_identity, settings, "Dune")
        assert result.status == "unlinked_user"
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_find_user_request_empty_title(
    mock_client: AsyncMock, mock_identity: MagicMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")

    token = current_telegram_user_id.set(1)
    try:
        result = await find_user_request(mock_client, mock_identity, settings, "   ")
        assert result.status == "empty_input"
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_find_user_request_exact_match_returns_request(
    mock_client: AsyncMock, mock_identity: MagicMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {"results": [{"id": 7}]}

    req_resp = MagicMock()
    req_resp.status_code = 200
    req_resp.json.return_value = {
        "results": [{"id": 1, "status": 2, "media": {"title": "Severance", "status": 5}}]
    }

    mock_client.get.side_effect = [user_resp, req_resp]

    token = current_telegram_user_id.set(1)
    try:
        result = await find_user_request(mock_client, mock_identity, settings, "severance")
        assert result.status == "ok"
        assert result.request is not None
        assert result.request["media"]["title"] == "Severance"
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_find_user_request_no_match(
    mock_client: AsyncMock, mock_identity: MagicMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {"results": [{"id": 7}]}

    req_resp = MagicMock()
    req_resp.status_code = 200
    req_resp.json.return_value = {
        "results": [{"id": 1, "status": 2, "media": {"title": "Breaking Bad", "status": 5}}]
    }

    mock_client.get.side_effect = [user_resp, req_resp]

    token = current_telegram_user_id.set(1)
    try:
        result = await find_user_request(mock_client, mock_identity, settings, "X-Files 1999")
        assert result.status == "no_match"
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_find_user_request_multi_match(
    mock_client: AsyncMock, mock_identity: MagicMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {"results": [{"id": 7}]}

    req_resp = MagicMock()
    req_resp.status_code = 200
    req_resp.json.return_value = {
        "results": [
            {"id": 1, "status": 2, "media": {"title": "Dune Part One", "status": 5}},
            {"id": 2, "status": 2, "media": {"title": "Dune Part Two", "status": 3}},
        ]
    }

    mock_client.get.side_effect = [user_resp, req_resp]

    token = current_telegram_user_id.set(1)
    try:
        result = await find_user_request(mock_client, mock_identity, settings, "Dune")
        assert result.status == "multi_match"
        assert result.candidates is not None
        assert len(result.candidates) == 2
    finally:
        current_telegram_user_id.reset(token)


# ---------------------------------------------------------------------------
# _title_match_score / _fuzzy_match_titles
#
# Bug found live: asking about "the flash" (never requested) returned
# completely unrelated titles ("Meet the Parents", "Meet the Fockers") as
# candidates. Plain difflib.SequenceMatcher.ratio() scored those ~0.48 —
# almost identical to the ~0.47 a *correct* short query like "dune" scores
# against "dune part one" — so no single cutoff could separate the two.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "candidate"),
    [
        ("dune", "dune part one"),
        ("dune", "dune part two"),
        ("dune part 2", "dune part two"),
        ("severance", "sevarance"),
        ("the bear", "bear"),
        ("project runwa", "project runway"),
    ],
)
def test_title_match_score_accepts_real_matches(query: str, candidate: str) -> None:
    assert _title_match_score(query, candidate) >= 0.6


@pytest.mark.parametrize(
    ("query", "candidate"),
    [
        ("the flash", "meet the fockers"),
        ("the flash", "meet the parents"),
        ("the flash", "greatest showman"),
        ("severance", "the office"),
    ],
)
def test_title_match_score_rejects_unrelated_titles(query: str, candidate: str) -> None:
    assert _title_match_score(query, candidate) < 0.6


def test_fuzzy_match_titles_excludes_unrelated_short_words() -> None:
    candidates = ["meet the parents", "meet the fockers", "greatest showman"]
    assert _fuzzy_match_titles("the flash", candidates) == []


def test_fuzzy_match_titles_finds_short_partial_query() -> None:
    candidates = ["dune part one", "dune part two", "severance"]
    matches = _fuzzy_match_titles("dune", candidates)
    assert set(matches) == {"dune part one", "dune part two"}


@pytest.mark.asyncio
async def test_find_user_request_no_match_ignores_unrelated_titles(
    mock_client: AsyncMock, mock_identity: MagicMock, settings: Settings
) -> None:
    """The exact live bug: a query for a title the user never requested must
    not surface unrelated titles as candidates just because they share a
    common short word."""
    mock_identity.get_link = AsyncMock(return_value="alice")

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {"results": [{"id": 7}]}

    req_resp = MagicMock()
    req_resp.status_code = 200
    req_resp.json.return_value = {
        "results": [
            {"id": 1, "status": 2, "media": {"title": "Meet the Parents", "status": 5}},
            {"id": 2, "status": 2, "media": {"title": "Meet the Fockers", "status": 5}},
        ]
    }

    mock_client.get.side_effect = [user_resp, req_resp]

    token = current_telegram_user_id.set(1)
    try:
        result = await find_user_request(mock_client, mock_identity, settings, "the flash")
    finally:
        current_telegram_user_id.reset(token)

    assert result.status == "no_match"


@pytest.mark.asyncio
async def test_find_user_request_http_error_on_requests_fetch(
    mock_client: AsyncMock, mock_identity: MagicMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {"results": [{"id": 7}]}

    req_resp = MagicMock()
    req_resp.status_code = 503

    mock_client.get.side_effect = [user_resp, req_resp]

    token = current_telegram_user_id.set(1)
    try:
        result = await find_user_request(mock_client, mock_identity, settings, "Severance")
        assert result.status == "http_error"
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_find_user_request_non_dict_response(
    mock_client: AsyncMock, mock_identity: MagicMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {"results": [{"id": 7}]}

    req_resp = MagicMock()
    req_resp.status_code = 200
    req_resp.json.return_value = ["not", "a", "dict"]

    mock_client.get.side_effect = [user_resp, req_resp]

    token = current_telegram_user_id.set(1)
    try:
        result = await find_user_request(mock_client, mock_identity, settings, "Severance")
        assert result.status == "parse_error"
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_find_user_request_non_list_results(
    mock_client: AsyncMock, mock_identity: MagicMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {"results": [{"id": 7}]}

    req_resp = MagicMock()
    req_resp.status_code = 200
    req_resp.json.return_value = {"results": "not-a-list"}

    mock_client.get.side_effect = [user_resp, req_resp]

    token = current_telegram_user_id.set(1)
    try:
        result = await find_user_request(mock_client, mock_identity, settings, "Severance")
        assert result.status == "parse_error"
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_find_user_request_skips_malformed_entries(
    mock_client: AsyncMock, mock_identity: MagicMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {"results": [{"id": 7}]}

    req_resp = MagicMock()
    req_resp.status_code = 200
    req_resp.json.return_value = {
        "results": [
            "not-a-dict",
            {"id": 1, "status": 2, "media": "not-a-dict-either"},
            {"id": 2, "status": 2, "media": {"title": "Severance", "status": 5}},
        ]
    }

    mock_client.get.side_effect = [user_resp, req_resp]

    token = current_telegram_user_id.set(1)
    try:
        result = await find_user_request(mock_client, mock_identity, settings, "severance")
        assert result.status == "ok"
        assert result.request is not None
        assert result.request["media"]["title"] == "Severance"
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_find_user_request_year_stripped_from_query(
    mock_client: AsyncMock, mock_identity: MagicMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {"results": [{"id": 7}]}

    req_resp = MagicMock()
    req_resp.status_code = 200
    req_resp.json.return_value = {
        "results": [{"id": 1, "status": 2, "media": {"title": "Severance", "status": 5}}]
    }

    mock_client.get.side_effect = [user_resp, req_resp]

    token = current_telegram_user_id.set(1)
    try:
        result = await find_user_request(mock_client, mock_identity, settings, "Severance (2022)")
        assert result.status == "ok"
        assert result.request is not None
        assert result.request["media"]["title"] == "Severance"
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_find_user_request_title_that_is_only_a_year(
    mock_client: AsyncMock, mock_identity: MagicMock, settings: Settings
) -> None:
    """Regression: "1917" is a real movie title, not just a year suffix to
    strip. Stripping it to "" made every candidate tie at the same fuzzy
    score, so the match was effectively random instead of picking "1917"."""
    mock_identity.get_link = AsyncMock(return_value="alice")

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {"results": [{"id": 7}]}

    req_resp = MagicMock()
    req_resp.status_code = 200
    req_resp.json.return_value = {
        "results": [
            {"id": 1, "status": 2, "media": {"title": "1917", "status": 5}},
            {"id": 2, "status": 2, "media": {"title": "Severance", "status": 3}},
        ]
    }

    mock_client.get.side_effect = [user_resp, req_resp]

    token = current_telegram_user_id.set(1)
    try:
        result = await find_user_request(mock_client, mock_identity, settings, "1917")
        assert result.status == "ok"
        assert result.request is not None
        assert result.request["media"]["title"] == "1917"
    finally:
        current_telegram_user_id.reset(token)


# ---------------------------------------------------------------------------
# _fetch_media_title / enrich_titles_with_names
#
# Real Overseerr responses never embed a title or name on request/media
# objects — only tmdbId/tvdbId. These must be resolved separately via the
# per-item /movie or /tv detail endpoint.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_media_title_movie(mock_client: AsyncMock) -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"id": 194, "title": "Amélie"}
    mock_client.get.return_value = resp

    title = await _fetch_media_title(mock_client, "http://overseerr:5055", "movie", 194)

    assert title == "Amélie"
    mock_client.get.assert_awaited_once_with("http://overseerr:5055/api/v1/movie/194")


@pytest.mark.asyncio
async def test_fetch_media_title_tv_uses_name_field(mock_client: AsyncMock) -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"id": 1685, "name": "Project Runway"}
    mock_client.get.return_value = resp

    title = await _fetch_media_title(mock_client, "http://overseerr:5055", "tv", 1685)

    assert title == "Project Runway"
    mock_client.get.assert_awaited_once_with("http://overseerr:5055/api/v1/tv/1685")


@pytest.mark.asyncio
async def test_fetch_media_title_returns_none_on_http_error(mock_client: AsyncMock) -> None:
    mock_client.get.side_effect = httpx.HTTPError("boom")

    title = await _fetch_media_title(mock_client, "http://overseerr:5055", "movie", 194)

    assert title is None


@pytest.mark.asyncio
async def test_fetch_media_title_returns_none_on_non_200(mock_client: AsyncMock) -> None:
    resp = MagicMock()
    resp.status_code = 404
    mock_client.get.return_value = resp

    title = await _fetch_media_title(mock_client, "http://overseerr:5055", "movie", 194)

    assert title is None


@pytest.mark.asyncio
async def test_fetch_media_title_returns_none_on_malformed_json(mock_client: AsyncMock) -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("bad json")
    mock_client.get.return_value = resp

    title = await _fetch_media_title(mock_client, "http://overseerr:5055", "movie", 194)

    assert title is None


@pytest.mark.asyncio
async def test_enrich_titles_skips_entries_without_tmdb_id(mock_client: AsyncMock) -> None:
    requests_list = [{"id": 1, "media": {"mediaType": "movie"}}]

    await enrich_titles_with_names(mock_client, "http://overseerr:5055", requests_list)

    mock_client.get.assert_not_awaited()
    assert "title" not in requests_list[0]["media"]


@pytest.mark.asyncio
async def test_enrich_titles_skips_non_dict_entries(mock_client: AsyncMock) -> None:
    requests_list: list[object] = ["not-a-dict", 42, None]

    await enrich_titles_with_names(mock_client, "http://overseerr:5055", requests_list)

    mock_client.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_find_user_request_resolves_title_when_media_lacks_one(
    mock_client: AsyncMock, mock_identity: MagicMock, settings: Settings
) -> None:
    """The real bug: Overseerr's request list has no title, so without
    resolving it first, fuzzy-matching against user input can never match
    anything — every real request was silently unmatchable."""
    mock_identity.get_link = AsyncMock(return_value="alice")

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {"results": [{"id": 7}]}

    req_resp = MagicMock()
    req_resp.status_code = 200
    req_resp.json.return_value = {
        "results": [
            {"id": 1, "status": 2, "media": {"mediaType": "tv", "tmdbId": 1685, "status": 5}}
        ]
    }

    tv_detail_resp = MagicMock()
    tv_detail_resp.status_code = 200
    tv_detail_resp.json.return_value = {"id": 1685, "name": "Severance"}

    mock_client.get.side_effect = [user_resp, req_resp, tv_detail_resp]

    token = current_telegram_user_id.set(1)
    try:
        result = await find_user_request(mock_client, mock_identity, settings, "severance")
    finally:
        current_telegram_user_id.reset(token)

    assert result.status == "ok"
    assert result.request is not None
    assert result.request["media"]["name"] == "Severance"
    mock_client.get.assert_any_call("http://overseerr:5055/api/v1/tv/1685")
