from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from cleanrr.config import Settings
from cleanrr.identity import Identity
from cleanrr.tools._context import current_telegram_user_id
from cleanrr.tools.sonarr_write import build_tools


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "telegram_bot_token": "test_token",
        "anthropic_api_key": "sk-test",
        "overseerr_url": "http://overseerr:5055",
        "overseerr_api_key": "ov-key",
        "sonarr_url": "http://sonarr:8989",
        "sonarr_api_key": "so-key",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def mock_identity() -> MagicMock:
    ident = MagicMock(spec=Identity)
    ident.get_link = AsyncMock(return_value="alice")
    return ident


@pytest.fixture
def mock_sonarr() -> AsyncMock:
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


def _overseerr_show_requests(title: str = "The Bear", tvdb_id: int | None = 555) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    media: dict[str, object] = {"name": title}
    if tvdb_id is not None:
        media["tvdbId"] = tvdb_id
    resp.json.return_value = {"results": [{"id": 1, "media": media}]}
    return resp


def _sonarr_series(series_id: int = 77, title: str = "The Bear") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = [{"id": series_id, "title": title}]
    return resp


def _sonarr_command_ok() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 201
    return resp


@pytest.mark.asyncio
async def test_happy_path_triggers_series_search(
    mock_identity: MagicMock,
    mock_sonarr: AsyncMock,
    mock_overseerr: AsyncMock,
    settings: Settings,
) -> None:
    mock_overseerr.get.side_effect = [_overseerr_user_resolve(), _overseerr_show_requests()]
    mock_sonarr.get.return_value = _sonarr_series(series_id=77, title="The Bear")
    mock_sonarr.post.return_value = _sonarr_command_ok()

    tools = build_tools(mock_sonarr, mock_overseerr, mock_identity, settings)
    assert len(tools) == 1
    tool_fn = tools[0]
    assert tool_fn.name == "force_research_show"

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"title": "The Bear"})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is False
    assert "The Bear" in result["content"][0]["text"]
    cmd_call = mock_sonarr.post.await_args
    assert cmd_call.kwargs["json"] == {"name": "SeriesSearch", "seriesId": 77}


@pytest.mark.asyncio
async def test_not_configured_when_sonarr_missing(
    mock_identity: MagicMock, mock_sonarr: AsyncMock, mock_overseerr: AsyncMock
) -> None:
    unconfigured = _settings(sonarr_url=None, sonarr_api_key=None)
    tools = build_tools(mock_sonarr, mock_overseerr, mock_identity, unconfigured)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"title": "The Bear"})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True
    mock_sonarr.post.assert_not_called()


@pytest.mark.asyncio
async def test_movie_passed_in_returns_friendly_error(
    mock_identity: MagicMock,
    mock_sonarr: AsyncMock,
    mock_overseerr: AsyncMock,
    settings: Settings,
) -> None:
    # tmdbId only, no tvdbId
    movie_request = MagicMock()
    movie_request.status_code = 200
    movie_request.json.return_value = {
        "results": [{"id": 1, "media": {"title": "Dune", "tmdbId": 12345}}]
    }
    mock_overseerr.get.side_effect = [_overseerr_user_resolve(), movie_request]

    tools = build_tools(mock_sonarr, mock_overseerr, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"title": "Dune"})
    finally:
        current_telegram_user_id.reset(token)

    assert "movie" in result["content"][0]["text"].lower()
    mock_sonarr.post.assert_not_called()


@pytest.mark.asyncio
async def test_show_not_in_sonarr_yet(
    mock_identity: MagicMock,
    mock_sonarr: AsyncMock,
    mock_overseerr: AsyncMock,
    settings: Settings,
) -> None:
    mock_overseerr.get.side_effect = [_overseerr_user_resolve(), _overseerr_show_requests()]
    empty = MagicMock()
    empty.status_code = 200
    empty.json.return_value = []
    mock_sonarr.get.return_value = empty

    tools = build_tools(mock_sonarr, mock_overseerr, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"title": "The Bear"})
    finally:
        current_telegram_user_id.reset(token)

    assert "hasn't picked it up" in result["content"][0]["text"].lower()
    mock_sonarr.post.assert_not_called()


@pytest.mark.asyncio
async def test_sonarr_series_lookup_5xx(
    mock_identity: MagicMock,
    mock_sonarr: AsyncMock,
    mock_overseerr: AsyncMock,
    settings: Settings,
) -> None:
    mock_overseerr.get.side_effect = [_overseerr_user_resolve(), _overseerr_show_requests()]
    bad = MagicMock()
    bad.status_code = 500
    mock_sonarr.get.return_value = bad

    tools = build_tools(mock_sonarr, mock_overseerr, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"title": "The Bear"})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True


@pytest.mark.asyncio
async def test_sonarr_series_lookup_network_error(
    mock_identity: MagicMock,
    mock_sonarr: AsyncMock,
    mock_overseerr: AsyncMock,
    settings: Settings,
) -> None:
    mock_overseerr.get.side_effect = [_overseerr_user_resolve(), _overseerr_show_requests()]
    mock_sonarr.get.side_effect = httpx.RequestError("boom")

    tools = build_tools(mock_sonarr, mock_overseerr, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"title": "The Bear"})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True


@pytest.mark.asyncio
async def test_sonarr_command_5xx(
    mock_identity: MagicMock,
    mock_sonarr: AsyncMock,
    mock_overseerr: AsyncMock,
    settings: Settings,
) -> None:
    mock_overseerr.get.side_effect = [_overseerr_user_resolve(), _overseerr_show_requests()]
    mock_sonarr.get.return_value = _sonarr_series()
    bad = MagicMock()
    bad.status_code = 500
    mock_sonarr.post.return_value = bad

    tools = build_tools(mock_sonarr, mock_overseerr, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"title": "The Bear"})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True
    assert "500" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_sonarr_series_lookup_malformed_json(
    mock_identity: MagicMock,
    mock_sonarr: AsyncMock,
    mock_overseerr: AsyncMock,
    settings: Settings,
) -> None:
    mock_overseerr.get.side_effect = [_overseerr_user_resolve(), _overseerr_show_requests()]
    bad = MagicMock()
    bad.status_code = 200
    bad.json.side_effect = ValueError("bad json")
    mock_sonarr.get.return_value = bad

    tools = build_tools(mock_sonarr, mock_overseerr, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"title": "The Bear"})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True


@pytest.mark.asyncio
async def test_sonarr_series_lookup_missing_series_id(
    mock_identity: MagicMock,
    mock_sonarr: AsyncMock,
    mock_overseerr: AsyncMock,
    settings: Settings,
) -> None:
    mock_overseerr.get.side_effect = [_overseerr_user_resolve(), _overseerr_show_requests()]
    weird = MagicMock()
    weird.status_code = 200
    weird.json.return_value = [{"title": "The Bear"}]  # no id field
    mock_sonarr.get.return_value = weird

    tools = build_tools(mock_sonarr, mock_overseerr, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"title": "The Bear"})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True
    mock_sonarr.post.assert_not_called()


@pytest.mark.asyncio
async def test_sonarr_command_network_error(
    mock_identity: MagicMock,
    mock_sonarr: AsyncMock,
    mock_overseerr: AsyncMock,
    settings: Settings,
) -> None:
    mock_overseerr.get.side_effect = [_overseerr_user_resolve(), _overseerr_show_requests()]
    mock_sonarr.get.return_value = _sonarr_series()
    mock_sonarr.post.side_effect = httpx.RequestError("boom")

    tools = build_tools(mock_sonarr, mock_overseerr, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"title": "The Bear"})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True


@pytest.mark.asyncio
async def test_sonarr_lookup_first_entry_not_dict(
    mock_identity: MagicMock,
    mock_sonarr: AsyncMock,
    mock_overseerr: AsyncMock,
    settings: Settings,
) -> None:
    mock_overseerr.get.side_effect = [_overseerr_user_resolve(), _overseerr_show_requests()]
    weird = MagicMock()
    weird.status_code = 200
    weird.json.return_value = ["not a dict"]
    mock_sonarr.get.return_value = weird

    tools = build_tools(mock_sonarr, mock_overseerr, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"title": "The Bear"})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True


@pytest.mark.asyncio
async def test_unlinked_user_returns_error_without_sonarr_calls(
    mock_identity: MagicMock,
    mock_sonarr: AsyncMock,
    mock_overseerr: AsyncMock,
    settings: Settings,
) -> None:
    mock_identity.get_link = AsyncMock(return_value=None)

    tools = build_tools(mock_sonarr, mock_overseerr, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"title": "The Bear"})
    finally:
        current_telegram_user_id.reset(token)

    assert "link" in result["content"][0]["text"].lower()
    mock_sonarr.post.assert_not_called()
    mock_sonarr.get.assert_not_called()
