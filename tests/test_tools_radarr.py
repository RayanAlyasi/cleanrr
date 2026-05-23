from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from cleanrr.config import Settings
from cleanrr.identity import Identity
from cleanrr.tools._context import current_telegram_user_id
from cleanrr.tools.radarr import build_tools


def _settings(**overrides: object) -> Settings:
    base = {
        "telegram_bot_token": "test_token",
        "anthropic_api_key": "sk-test",
        "_env_file": None,
        "overseerr_url": "http://overseerr:5055",
        "overseerr_api_key": "test_overseerr_key",
        "radarr_url": "http://radarr:7878",
        "radarr_api_key": "test_radarr_key",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def mock_identity() -> MagicMock:
    return MagicMock(spec=Identity)


@pytest.fixture
def mock_radarr_client() -> AsyncMock:
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def mock_overseerr_client() -> AsyncMock:
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def settings() -> Settings:
    return _settings()


def _make_overseerr_ok(
    tmdb_id: int | None = 438631, title: str = "Dune"
) -> tuple[MagicMock, MagicMock]:
    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {"results": [{"id": 7}]}

    req_resp = MagicMock()
    req_resp.status_code = 200
    media: dict[str, object] = {"title": title, "status": 5}
    if tmdb_id is not None:
        media["tmdbId"] = tmdb_id
    req_resp.json.return_value = {"results": [{"id": 1, "status": 2, "media": media}]}
    return user_resp, req_resp


@pytest.mark.asyncio
async def test_get_movie_status_radarr_not_configured(
    mock_radarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
) -> None:
    settings = _settings(radarr_url=None, radarr_api_key=None)
    tools = build_tools(mock_radarr_client, mock_overseerr_client, mock_identity, settings)
    get_movie_status = tools[0]

    result = await get_movie_status.handler({"title": "Dune"})
    assert result["is_error"] is True
    assert "Radarr isn't configured" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_movie_status_overseerr_not_configured(
    mock_radarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
) -> None:
    settings = _settings(overseerr_url=None, overseerr_api_key=None)
    tools = build_tools(mock_radarr_client, mock_overseerr_client, mock_identity, settings)
    get_movie_status = tools[0]

    result = await get_movie_status.handler({"title": "Dune"})
    assert result["is_error"] is True
    assert "Overseerr isn't configured" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_movie_status_context_missing(
    mock_radarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    tools = build_tools(mock_radarr_client, mock_overseerr_client, mock_identity, settings)
    get_movie_status = tools[0]

    result = await get_movie_status.handler({"title": "Dune"})
    assert result["is_error"] is True
    assert "Internal error" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_movie_status_unlinked_user(
    mock_radarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    mock_identity.get_link = AsyncMock(return_value=None)
    tools = build_tools(mock_radarr_client, mock_overseerr_client, mock_identity, settings)
    get_movie_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_movie_status.handler({"title": "Dune"})
        assert "linked your Overseerr account" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_movie_status_empty_input(
    mock_radarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")
    tools = build_tools(mock_radarr_client, mock_overseerr_client, mock_identity, settings)
    get_movie_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_movie_status.handler({"title": "  "})
        assert "which title" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_movie_status_user_not_found(
    mock_radarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")
    user_resp = MagicMock()
    user_resp.status_code = 404
    mock_overseerr_client.get.return_value = user_resp

    tools = build_tools(mock_radarr_client, mock_overseerr_client, mock_identity, settings)
    get_movie_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_movie_status.handler({"title": "Dune"})
        assert "Couldn't find your Overseerr account" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_movie_status_overseerr_http_error(
    mock_radarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")
    user_resp = MagicMock()
    user_resp.status_code = 500
    mock_overseerr_client.get.return_value = user_resp

    tools = build_tools(mock_radarr_client, mock_overseerr_client, mock_identity, settings)
    get_movie_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_movie_status.handler({"title": "Dune"})
        assert "Couldn't reach Overseerr" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_movie_status_overseerr_parse_error(
    mock_radarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")
    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.side_effect = ValueError("bad json")
    mock_overseerr_client.get.return_value = user_resp

    tools = build_tools(mock_radarr_client, mock_overseerr_client, mock_identity, settings)
    get_movie_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_movie_status.handler({"title": "Dune"})
        assert "Unexpected response format from Overseerr" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_movie_status_no_match(
    mock_radarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {"results": [{"id": 7}]}

    req_resp = MagicMock()
    req_resp.status_code = 200
    req_resp.json.return_value = {
        "results": [{"id": 1, "status": 2, "media": {"title": "Dune", "status": 5, "tmdbId": 1}}]
    }

    mock_overseerr_client.get.side_effect = [user_resp, req_resp]

    tools = build_tools(mock_radarr_client, mock_overseerr_client, mock_identity, settings)
    get_movie_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_movie_status.handler({"title": "Completely Different Movie"})
        assert "couldn't find a request" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_movie_status_multi_match(
    mock_radarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {"results": [{"id": 7}]}

    req_resp = MagicMock()
    req_resp.status_code = 200
    req_resp.json.return_value = {
        "results": [
            {
                "id": 1,
                "status": 2,
                "media": {"title": "Dune Part One", "status": 5, "tmdbId": 438631},
            },
            {
                "id": 2,
                "status": 2,
                "media": {"title": "Dune Part Two", "status": 5, "tmdbId": 693134},
            },
        ]
    }

    mock_overseerr_client.get.side_effect = [user_resp, req_resp]

    tools = build_tools(mock_radarr_client, mock_overseerr_client, mock_identity, settings)
    get_movie_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_movie_status.handler({"title": "Dune"})
        text = result["content"][0]["text"]
        assert "possible matches" in text
        assert "Dune Part One" in text
        assert "Dune Part Two" in text
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_movie_status_not_a_movie(
    mock_radarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    """Overseerr request has tvdbId but no tmdbId → not_a_movie."""
    mock_identity.get_link = AsyncMock(return_value="alice")

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {"results": [{"id": 7}]}

    req_resp = MagicMock()
    req_resp.status_code = 200
    req_resp.json.return_value = {
        "results": [
            {
                "id": 1,
                "status": 2,
                "media": {"title": "The Bear", "status": 5, "tvdbId": 123},
            }
        ]
    }

    mock_overseerr_client.get.side_effect = [user_resp, req_resp]

    tools = build_tools(mock_radarr_client, mock_overseerr_client, mock_identity, settings)
    get_movie_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_movie_status.handler({"title": "The Bear"})
        assert "TV show" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_movie_status_not_in_radarr(
    mock_radarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    """Radarr returns empty array → not_in_radarr."""
    mock_identity.get_link = AsyncMock(return_value="alice")

    user_resp, req_resp = _make_overseerr_ok()
    mock_overseerr_client.get.side_effect = [user_resp, req_resp]

    movie_resp = MagicMock()
    movie_resp.status_code = 200
    movie_resp.json.return_value = []
    mock_radarr_client.get.return_value = movie_resp

    tools = build_tools(mock_radarr_client, mock_overseerr_client, mock_identity, settings)
    get_movie_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_movie_status.handler({"title": "Dune"})
        assert "hasn't picked it up" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_movie_status_radarr_http_error(
    mock_radarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    """500 on movie fetch → http_error."""
    mock_identity.get_link = AsyncMock(return_value="alice")

    user_resp, req_resp = _make_overseerr_ok()
    mock_overseerr_client.get.side_effect = [user_resp, req_resp]

    movie_resp = MagicMock()
    movie_resp.status_code = 500
    mock_radarr_client.get.return_value = movie_resp

    tools = build_tools(mock_radarr_client, mock_overseerr_client, mock_identity, settings)
    get_movie_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_movie_status.handler({"title": "Dune"})
        assert result["is_error"] is True
        assert "Couldn't reach Radarr" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_movie_status_radarr_parse_error(
    mock_radarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    """Malformed JSON from Radarr movie endpoint → parse_error."""
    mock_identity.get_link = AsyncMock(return_value="alice")

    user_resp, req_resp = _make_overseerr_ok()
    mock_overseerr_client.get.side_effect = [user_resp, req_resp]

    movie_resp = MagicMock()
    movie_resp.status_code = 200
    movie_resp.json.side_effect = ValueError("bad json")
    mock_radarr_client.get.return_value = movie_resp

    tools = build_tools(mock_radarr_client, mock_overseerr_client, mock_identity, settings)
    get_movie_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_movie_status.handler({"title": "Dune"})
        assert result["is_error"] is True
        assert "Unexpected response format from Radarr" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_movie_status_movie_not_dict(
    mock_radarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    """Radarr returns list whose first element is not a dict → parse_error."""
    mock_identity.get_link = AsyncMock(return_value="alice")

    user_resp, req_resp = _make_overseerr_ok()
    mock_overseerr_client.get.side_effect = [user_resp, req_resp]

    movie_resp = MagicMock()
    movie_resp.status_code = 200
    movie_resp.json.return_value = ["not a dict"]
    mock_radarr_client.get.return_value = movie_resp

    tools = build_tools(mock_radarr_client, mock_overseerr_client, mock_identity, settings)
    get_movie_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_movie_status.handler({"title": "Dune"})
        assert result["is_error"] is True
        assert "Unexpected response format from Radarr" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_movie_status_movie_missing_id(
    mock_radarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    """Radarr movie dict missing id field → parse_error."""
    mock_identity.get_link = AsyncMock(return_value="alice")

    user_resp, req_resp = _make_overseerr_ok()
    mock_overseerr_client.get.side_effect = [user_resp, req_resp]

    movie_resp = MagicMock()
    movie_resp.status_code = 200
    movie_resp.json.return_value = [{"title": "Dune"}]
    mock_radarr_client.get.return_value = movie_resp

    tools = build_tools(mock_radarr_client, mock_overseerr_client, mock_identity, settings)
    get_movie_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_movie_status.handler({"title": "Dune"})
        assert result["is_error"] is True
        assert "Unexpected response format from Radarr" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_movie_status_downloaded(
    mock_radarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    """hasFile=True → 'Dune (2021) is downloaded.'"""
    mock_identity.get_link = AsyncMock(return_value="alice")

    user_resp, req_resp = _make_overseerr_ok()
    mock_overseerr_client.get.side_effect = [user_resp, req_resp]

    movie_resp = MagicMock()
    movie_resp.status_code = 200
    movie_resp.json.return_value = [{"id": 42, "title": "Dune", "year": 2021, "hasFile": True}]

    queue_resp = MagicMock()
    queue_resp.status_code = 200
    queue_resp.json.return_value = {"records": []}

    mock_radarr_client.get.side_effect = [movie_resp, queue_resp]

    tools = build_tools(mock_radarr_client, mock_overseerr_client, mock_identity, settings)
    get_movie_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_movie_status.handler({"title": "Dune"})
        assert "Dune (2021) is downloaded." in result["content"][0]["text"]
        assert result["is_error"] is False
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_movie_status_downloading(
    mock_radarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    """hasFile=False, queue has records → 'downloading.'"""
    mock_identity.get_link = AsyncMock(return_value="alice")

    user_resp, req_resp = _make_overseerr_ok(title="The Batman")
    mock_overseerr_client.get.side_effect = [user_resp, req_resp]

    movie_resp = MagicMock()
    movie_resp.status_code = 200
    movie_resp.json.return_value = [
        {"id": 42, "title": "The Batman", "year": 2022, "hasFile": False}
    ]

    queue_resp = MagicMock()
    queue_resp.status_code = 200
    queue_resp.json.return_value = {"records": [{}]}

    mock_radarr_client.get.side_effect = [movie_resp, queue_resp]

    tools = build_tools(mock_radarr_client, mock_overseerr_client, mock_identity, settings)
    get_movie_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_movie_status.handler({"title": "The Batman"})
        assert "The Batman (2022): downloading." in result["content"][0]["text"]
        assert result["is_error"] is False
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_movie_status_nothing_yet(
    mock_radarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    """hasFile=False, empty queue → 'nothing yet'"""
    mock_identity.get_link = AsyncMock(return_value="alice")

    user_resp, req_resp = _make_overseerr_ok(title="Oppenheimer")
    mock_overseerr_client.get.side_effect = [user_resp, req_resp]

    movie_resp = MagicMock()
    movie_resp.status_code = 200
    movie_resp.json.return_value = [
        {"id": 42, "title": "Oppenheimer", "year": 2023, "hasFile": False}
    ]

    queue_resp = MagicMock()
    queue_resp.status_code = 200
    queue_resp.json.return_value = {"records": []}

    mock_radarr_client.get.side_effect = [movie_resp, queue_resp]

    tools = build_tools(mock_radarr_client, mock_overseerr_client, mock_identity, settings)
    get_movie_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_movie_status.handler({"title": "Oppenheimer"})
        text = result["content"][0]["text"]
        assert "nothing yet" in text
        assert "Radarr is searching" in text
        assert result["is_error"] is False
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_movie_status_queue_fetch_fails_still_returns_movie(
    mock_radarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    """Queue 500 fallback — still returns movie-level summary."""
    mock_identity.get_link = AsyncMock(return_value="alice")

    user_resp, req_resp = _make_overseerr_ok()
    mock_overseerr_client.get.side_effect = [user_resp, req_resp]

    movie_resp = MagicMock()
    movie_resp.status_code = 200
    movie_resp.json.return_value = [{"id": 42, "title": "Dune", "year": 2021, "hasFile": True}]

    queue_resp = MagicMock()
    queue_resp.status_code = 500

    mock_radarr_client.get.side_effect = [movie_resp, queue_resp]

    tools = build_tools(mock_radarr_client, mock_overseerr_client, mock_identity, settings)
    get_movie_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_movie_status.handler({"title": "Dune"})
        assert "Dune (2021) is downloaded." in result["content"][0]["text"]
        assert result["is_error"] is False
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_movie_status_radarr_http_exception(
    mock_radarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    """httpx.ConnectError → http_error."""
    mock_identity.get_link = AsyncMock(return_value="alice")

    user_resp, req_resp = _make_overseerr_ok()
    mock_overseerr_client.get.side_effect = [user_resp, req_resp]
    mock_radarr_client.get.side_effect = httpx.ConnectError("connection refused")

    tools = build_tools(mock_radarr_client, mock_overseerr_client, mock_identity, settings)
    get_movie_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_movie_status.handler({"title": "Dune"})
        assert result["is_error"] is True
        assert "error occurred" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)
