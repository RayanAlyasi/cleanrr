from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from cleanrr.config import Settings
from cleanrr.identity import Identity
from cleanrr.tools._context import current_telegram_user_id
from cleanrr.tools.radarr_write import build_tools


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "telegram_bot_token": "test_token",
        "anthropic_api_key": "sk-test",
        "overseerr_url": "http://overseerr:5055",
        "overseerr_api_key": "ov-key",
        "radarr_url": "http://radarr:7878",
        "radarr_api_key": "ra-key",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def mock_identity() -> MagicMock:
    ident = MagicMock(spec=Identity)
    ident.get_link = AsyncMock(return_value="alice")
    return ident


@pytest.fixture
def mock_radarr() -> AsyncMock:
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def mock_overseerr() -> AsyncMock:
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def settings() -> Settings:
    return _settings()


def _overseerr_user_resolve(user_id: int = 42) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"results": [{"id": user_id}]}
    return resp


def _overseerr_requests(title: str = "Dune", tmdb_id: int | None = 12345) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    media: dict[str, object] = {"title": title}
    if tmdb_id is not None:
        media["tmdbId"] = tmdb_id
    resp.json.return_value = {"results": [{"id": 1, "media": media}]}
    return resp


def _radarr_movie(movie_id: int = 99, title: str = "Dune") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = [{"id": movie_id, "title": title}]
    return resp


def _radarr_command_ok() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 201
    return resp


@pytest.mark.asyncio
async def test_happy_path_triggers_search(
    mock_identity: MagicMock,
    mock_radarr: AsyncMock,
    mock_overseerr: AsyncMock,
    settings: Settings,
) -> None:
    mock_overseerr.get.side_effect = [_overseerr_user_resolve(), _overseerr_requests()]
    mock_radarr.get.return_value = _radarr_movie(movie_id=99, title="Dune")
    mock_radarr.post.return_value = _radarr_command_ok()

    tools = build_tools(mock_radarr, mock_overseerr, mock_identity, settings)
    assert len(tools) == 1
    tool_fn = tools[0]
    assert tool_fn.name == "force_research_movie"

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"title": "Dune"})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is False
    assert "Dune" in result["content"][0]["text"]
    # Verify the POST body was the right command shape
    cmd_call = mock_radarr.post.await_args
    assert cmd_call.kwargs["json"] == {"name": "MoviesSearch", "movieIds": [99]}


@pytest.mark.asyncio
async def test_not_configured_when_radarr_missing(
    mock_identity: MagicMock, mock_radarr: AsyncMock, mock_overseerr: AsyncMock
) -> None:
    unconfigured = _settings(radarr_url=None, radarr_api_key=None)
    tools = build_tools(mock_radarr, mock_overseerr, mock_identity, unconfigured)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"title": "Dune"})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True
    mock_radarr.post.assert_not_called()


@pytest.mark.asyncio
async def test_tv_show_passed_in_returns_friendly_error(
    mock_identity: MagicMock,
    mock_radarr: AsyncMock,
    mock_overseerr: AsyncMock,
    settings: Settings,
) -> None:
    # tvdbId only — no tmdbId
    show_request = MagicMock()
    show_request.status_code = 200
    show_request.json.return_value = {
        "results": [{"id": 1, "media": {"title": "The Bear", "tvdbId": 555}}]
    }
    mock_overseerr.get.side_effect = [_overseerr_user_resolve(), show_request]

    tools = build_tools(mock_radarr, mock_overseerr, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"title": "The Bear"})
    finally:
        current_telegram_user_id.reset(token)

    assert "tv show" in result["content"][0]["text"].lower()
    mock_radarr.post.assert_not_called()


@pytest.mark.asyncio
async def test_movie_not_in_radarr_yet(
    mock_identity: MagicMock,
    mock_radarr: AsyncMock,
    mock_overseerr: AsyncMock,
    settings: Settings,
) -> None:
    mock_overseerr.get.side_effect = [_overseerr_user_resolve(), _overseerr_requests()]
    empty = MagicMock()
    empty.status_code = 200
    empty.json.return_value = []
    mock_radarr.get.return_value = empty

    tools = build_tools(mock_radarr, mock_overseerr, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"title": "Dune"})
    finally:
        current_telegram_user_id.reset(token)

    assert "hasn't picked it up" in result["content"][0]["text"].lower()
    mock_radarr.post.assert_not_called()


@pytest.mark.asyncio
async def test_radarr_movie_lookup_5xx(
    mock_identity: MagicMock,
    mock_radarr: AsyncMock,
    mock_overseerr: AsyncMock,
    settings: Settings,
) -> None:
    mock_overseerr.get.side_effect = [_overseerr_user_resolve(), _overseerr_requests()]
    bad = MagicMock()
    bad.status_code = 500
    mock_radarr.get.return_value = bad

    tools = build_tools(mock_radarr, mock_overseerr, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"title": "Dune"})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True


@pytest.mark.asyncio
async def test_radarr_movie_lookup_network_error(
    mock_identity: MagicMock,
    mock_radarr: AsyncMock,
    mock_overseerr: AsyncMock,
    settings: Settings,
) -> None:
    mock_overseerr.get.side_effect = [_overseerr_user_resolve(), _overseerr_requests()]
    mock_radarr.get.side_effect = httpx.RequestError("boom")

    tools = build_tools(mock_radarr, mock_overseerr, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"title": "Dune"})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True


@pytest.mark.asyncio
async def test_radarr_command_5xx(
    mock_identity: MagicMock,
    mock_radarr: AsyncMock,
    mock_overseerr: AsyncMock,
    settings: Settings,
) -> None:
    mock_overseerr.get.side_effect = [_overseerr_user_resolve(), _overseerr_requests()]
    mock_radarr.get.return_value = _radarr_movie()
    bad = MagicMock()
    bad.status_code = 500
    mock_radarr.post.return_value = bad

    tools = build_tools(mock_radarr, mock_overseerr, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"title": "Dune"})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True
    assert "500" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_unlinked_user_returns_error_without_radarr_calls(
    mock_identity: MagicMock,
    mock_radarr: AsyncMock,
    mock_overseerr: AsyncMock,
    settings: Settings,
) -> None:
    mock_identity.get_link = AsyncMock(return_value=None)

    tools = build_tools(mock_radarr, mock_overseerr, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"title": "Dune"})
    finally:
        current_telegram_user_id.reset(token)

    assert "link" in result["content"][0]["text"].lower()
    mock_radarr.post.assert_not_called()
    mock_radarr.get.assert_not_called()


@pytest.mark.asyncio
async def test_radarr_movie_lookup_malformed_json(
    mock_identity: MagicMock,
    mock_radarr: AsyncMock,
    mock_overseerr: AsyncMock,
    settings: Settings,
) -> None:
    mock_overseerr.get.side_effect = [_overseerr_user_resolve(), _overseerr_requests()]
    bad = MagicMock()
    bad.status_code = 200
    bad.json.side_effect = ValueError("bad json")
    mock_radarr.get.return_value = bad

    tools = build_tools(mock_radarr, mock_overseerr, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"title": "Dune"})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True


@pytest.mark.asyncio
async def test_radarr_movie_lookup_missing_movie_id(
    mock_identity: MagicMock,
    mock_radarr: AsyncMock,
    mock_overseerr: AsyncMock,
    settings: Settings,
) -> None:
    mock_overseerr.get.side_effect = [_overseerr_user_resolve(), _overseerr_requests()]
    weird = MagicMock()
    weird.status_code = 200
    weird.json.return_value = [{"title": "Dune"}]  # no id field
    mock_radarr.get.return_value = weird

    tools = build_tools(mock_radarr, mock_overseerr, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"title": "Dune"})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True
    mock_radarr.post.assert_not_called()


@pytest.mark.asyncio
async def test_radarr_command_network_error(
    mock_identity: MagicMock,
    mock_radarr: AsyncMock,
    mock_overseerr: AsyncMock,
    settings: Settings,
) -> None:
    mock_overseerr.get.side_effect = [_overseerr_user_resolve(), _overseerr_requests()]
    mock_radarr.get.return_value = _radarr_movie()
    mock_radarr.post.side_effect = httpx.RequestError("boom")

    tools = build_tools(mock_radarr, mock_overseerr, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"title": "Dune"})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True


@pytest.mark.asyncio
async def test_radarr_lookup_first_entry_not_dict(
    mock_identity: MagicMock,
    mock_radarr: AsyncMock,
    mock_overseerr: AsyncMock,
    settings: Settings,
) -> None:
    mock_overseerr.get.side_effect = [_overseerr_user_resolve(), _overseerr_requests()]
    weird = MagicMock()
    weird.status_code = 200
    weird.json.return_value = ["not a dict"]
    mock_radarr.get.return_value = weird

    tools = build_tools(mock_radarr, mock_overseerr, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"title": "Dune"})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True


@pytest.mark.asyncio
async def test_command_201_and_202_both_treated_as_success(
    mock_identity: MagicMock,
    mock_radarr: AsyncMock,
    mock_overseerr: AsyncMock,
    settings: Settings,
) -> None:
    for status in (200, 201, 202):
        mock_overseerr.get.side_effect = [_overseerr_user_resolve(), _overseerr_requests()]
        mock_radarr.get.return_value = _radarr_movie()
        ok = MagicMock()
        ok.status_code = status
        mock_radarr.post.return_value = ok

        tools = build_tools(mock_radarr, mock_overseerr, mock_identity, settings)
        tool_fn = tools[0]

        token = current_telegram_user_id.set(123)
        try:
            result = await tool_fn.handler({"title": "Dune"})
        finally:
            current_telegram_user_id.reset(token)

        assert result["is_error"] is False, f"status {status} should be success"
