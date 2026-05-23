from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from cleanrr.config import Settings
from cleanrr.identity import Identity
from cleanrr.tools._context import current_telegram_user_id
from cleanrr.tools.sonarr import build_tools


def _settings(**overrides: object) -> Settings:
    base = {
        "telegram_bot_token": "test_token",
        "anthropic_api_key": "sk-test",
        "_env_file": None,
        "overseerr_url": "http://overseerr:5055",
        "overseerr_api_key": "test_overseerr_key",
        "sonarr_url": "http://sonarr:8989",
        "sonarr_api_key": "test_sonarr_key",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def mock_identity() -> MagicMock:
    return MagicMock(spec=Identity)


@pytest.fixture
def mock_sonarr_client() -> AsyncMock:
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def mock_overseerr_client() -> AsyncMock:
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def settings() -> Settings:
    return _settings()


@pytest.mark.asyncio
async def test_get_show_status_sonarr_not_configured(
    mock_sonarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
) -> None:
    settings = _settings(sonarr_url=None, sonarr_api_key=None)
    tools = build_tools(mock_sonarr_client, mock_overseerr_client, mock_identity, settings)
    get_show_status = tools[0]

    result = await get_show_status.handler({"title": "The Bear"})
    assert result["is_error"] is True
    assert "Sonarr isn't configured" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_show_status_not_a_show(
    mock_sonarr_client: AsyncMock,
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
        "results": [{"id": 1, "status": 2, "media": {"title": "Dune", "status": 5, "tvdbId": None}}]
    }

    mock_overseerr_client.get.side_effect = [user_resp, req_resp]

    tools = build_tools(mock_sonarr_client, mock_overseerr_client, mock_identity, settings)
    get_show_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_show_status.handler({"title": "Dune"})
        assert "movie" in result["content"][0]["text"].lower()
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_show_status_not_in_sonarr(
    mock_sonarr_client: AsyncMock,
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
            {"id": 1, "status": 2, "media": {"title": "The Bear", "status": 5, "tvdbId": 123}}
        ]
    }

    series_resp = MagicMock()
    series_resp.status_code = 200
    series_resp.json.return_value = []

    mock_overseerr_client.get.side_effect = [user_resp, req_resp]
    mock_sonarr_client.get.return_value = series_resp

    tools = build_tools(mock_sonarr_client, mock_overseerr_client, mock_identity, settings)
    get_show_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_show_status.handler({"title": "The Bear"})
        assert "hasn't picked it up" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_show_status_all_downloaded(
    mock_sonarr_client: AsyncMock,
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
                "media": {"title": "Breaking Bad", "status": 5, "tvdbId": 456},
            }
        ]
    }

    series_resp = MagicMock()
    series_resp.status_code = 200
    series_resp.json.return_value = [
        {
            "id": 1,
            "title": "Breaking Bad",
            "statistics": {"episodeCount": 62, "episodeFileCount": 62},
        }
    ]

    queue_resp = MagicMock()
    queue_resp.status_code = 200
    queue_resp.json.return_value = {"records": []}

    mock_overseerr_client.get.side_effect = [user_resp, req_resp]
    mock_sonarr_client.get.side_effect = [series_resp, queue_resp]

    tools = build_tools(mock_sonarr_client, mock_overseerr_client, mock_identity, settings)
    get_show_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_show_status.handler({"title": "Breaking Bad"})
        assert "All 62 episodes" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_show_status_partial_with_queue(
    mock_sonarr_client: AsyncMock,
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
            {"id": 1, "status": 2, "media": {"title": "The Bear", "status": 5, "tvdbId": 789}}
        ]
    }

    series_resp = MagicMock()
    series_resp.status_code = 200
    series_resp.json.return_value = [
        {
            "id": 2,
            "title": "The Bear",
            "statistics": {"episodeCount": 30, "episodeFileCount": 22},
        }
    ]

    queue_resp = MagicMock()
    queue_resp.status_code = 200
    queue_resp.json.return_value = {"records": [{}, {}]}

    mock_overseerr_client.get.side_effect = [user_resp, req_resp]
    mock_sonarr_client.get.side_effect = [series_resp, queue_resp]

    tools = build_tools(mock_sonarr_client, mock_overseerr_client, mock_identity, settings)
    get_show_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_show_status.handler({"title": "The Bear"})
        assert "22 of 30 episodes ready, 2 downloading" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_show_status_nothing_yet(
    mock_sonarr_client: AsyncMock,
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
            {"id": 1, "status": 2, "media": {"title": "New Show", "status": 5, "tvdbId": 999}}
        ]
    }

    series_resp = MagicMock()
    series_resp.status_code = 200
    series_resp.json.return_value = [
        {
            "id": 3,
            "title": "New Show",
            "statistics": {"episodeCount": 10, "episodeFileCount": 0},
        }
    ]

    queue_resp = MagicMock()
    queue_resp.status_code = 200
    queue_resp.json.return_value = {"records": []}

    mock_overseerr_client.get.side_effect = [user_resp, req_resp]
    mock_sonarr_client.get.side_effect = [series_resp, queue_resp]

    tools = build_tools(mock_sonarr_client, mock_overseerr_client, mock_identity, settings)
    get_show_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_show_status.handler({"title": "New Show"})
        assert "nothing downloaded yet" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_show_status_partial_no_queue(
    mock_sonarr_client: AsyncMock,
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
            {"id": 1, "status": 2, "media": {"title": "Severance", "status": 5, "tvdbId": 111}}
        ]
    }

    series_resp = MagicMock()
    series_resp.status_code = 200
    series_resp.json.return_value = [
        {
            "id": 4,
            "title": "Severance",
            "statistics": {"episodeCount": 25, "episodeFileCount": 15},
        }
    ]

    queue_resp = MagicMock()
    queue_resp.status_code = 200
    queue_resp.json.return_value = {"records": []}

    mock_overseerr_client.get.side_effect = [user_resp, req_resp]
    mock_sonarr_client.get.side_effect = [series_resp, queue_resp]

    tools = build_tools(mock_sonarr_client, mock_overseerr_client, mock_identity, settings)
    get_show_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_show_status.handler({"title": "Severance"})
        assert "15 of 25 episodes ready" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_show_status_queue_fetch_fails_still_returns_series(
    mock_sonarr_client: AsyncMock,
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
                "media": {"title": "Fallback Test", "status": 5, "tvdbId": 222},
            }
        ]
    }

    series_resp = MagicMock()
    series_resp.status_code = 200
    series_resp.json.return_value = [
        {
            "id": 5,
            "title": "Fallback Test",
            "statistics": {"episodeCount": 20, "episodeFileCount": 10},
        }
    ]

    queue_resp = MagicMock()
    queue_resp.status_code = 500

    mock_overseerr_client.get.side_effect = [user_resp, req_resp]
    mock_sonarr_client.get.side_effect = [series_resp, queue_resp]

    tools = build_tools(mock_sonarr_client, mock_overseerr_client, mock_identity, settings)
    get_show_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_show_status.handler({"title": "Fallback Test"})
        assert "10 of 20 episodes ready" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_show_status_overseerr_not_configured(
    mock_sonarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
) -> None:
    settings = _settings(overseerr_url=None, overseerr_api_key=None)
    tools = build_tools(mock_sonarr_client, mock_overseerr_client, mock_identity, settings)
    get_show_status = tools[0]

    result = await get_show_status.handler({"title": "The Bear"})
    assert result["is_error"] is True
    assert "Overseerr isn't configured" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_show_status_context_missing(
    mock_sonarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    tools = build_tools(mock_sonarr_client, mock_overseerr_client, mock_identity, settings)
    get_show_status = tools[0]
    result = await get_show_status.handler({"title": "The Bear"})
    assert result["is_error"] is True
    assert "Internal error" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_show_status_unlinked_user(
    mock_sonarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    mock_identity.get_link = AsyncMock(return_value=None)
    tools = build_tools(mock_sonarr_client, mock_overseerr_client, mock_identity, settings)
    get_show_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_show_status.handler({"title": "The Bear"})
        assert "linked your Overseerr account" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_show_status_empty_input(
    mock_sonarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")
    tools = build_tools(mock_sonarr_client, mock_overseerr_client, mock_identity, settings)
    get_show_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_show_status.handler({"title": "  "})
        assert "which title" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_show_status_user_not_found(
    mock_sonarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")
    user_resp = MagicMock()
    user_resp.status_code = 404
    mock_overseerr_client.get.return_value = user_resp

    tools = build_tools(mock_sonarr_client, mock_overseerr_client, mock_identity, settings)
    get_show_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_show_status.handler({"title": "The Bear"})
        assert "Couldn't find your Overseerr account" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_show_status_overseerr_http_error(
    mock_sonarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")
    user_resp = MagicMock()
    user_resp.status_code = 500
    mock_overseerr_client.get.return_value = user_resp

    tools = build_tools(mock_sonarr_client, mock_overseerr_client, mock_identity, settings)
    get_show_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_show_status.handler({"title": "The Bear"})
        assert "Couldn't reach Overseerr" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_show_status_overseerr_parse_error(
    mock_sonarr_client: AsyncMock,
    mock_overseerr_client: AsyncMock,
    mock_identity: MagicMock,
    settings: Settings,
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")
    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.side_effect = ValueError("bad json")
    mock_overseerr_client.get.return_value = user_resp

    tools = build_tools(mock_sonarr_client, mock_overseerr_client, mock_identity, settings)
    get_show_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_show_status.handler({"title": "The Bear"})
        assert "Unexpected response format from Overseerr" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_show_status_no_match(
    mock_sonarr_client: AsyncMock,
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
            {"id": 1, "status": 2, "media": {"title": "Severance", "status": 5, "tvdbId": 1}}
        ]
    }

    mock_overseerr_client.get.side_effect = [user_resp, req_resp]

    tools = build_tools(mock_sonarr_client, mock_overseerr_client, mock_identity, settings)
    get_show_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_show_status.handler({"title": "Completely Different Show"})
        assert "couldn't find a request" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_show_status_multi_match(
    mock_sonarr_client: AsyncMock,
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
                "media": {"title": "Dune Part One", "status": 5, "tvdbId": 1},
            },
            {
                "id": 2,
                "status": 2,
                "media": {"title": "Dune Part Two", "status": 5, "tvdbId": 2},
            },
        ]
    }

    mock_overseerr_client.get.side_effect = [user_resp, req_resp]

    tools = build_tools(mock_sonarr_client, mock_overseerr_client, mock_identity, settings)
    get_show_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_show_status.handler({"title": "Dune"})
        text = result["content"][0]["text"]
        assert "possible matches" in text
        assert "Dune Part One" in text
        assert "Dune Part Two" in text
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_show_status_series_http_error(
    mock_sonarr_client: AsyncMock,
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
            {"id": 1, "status": 2, "media": {"title": "The Bear", "status": 5, "tvdbId": 123}}
        ]
    }

    series_resp = MagicMock()
    series_resp.status_code = 500

    mock_overseerr_client.get.side_effect = [user_resp, req_resp]
    mock_sonarr_client.get.return_value = series_resp

    tools = build_tools(mock_sonarr_client, mock_overseerr_client, mock_identity, settings)
    get_show_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_show_status.handler({"title": "The Bear"})
        assert result["is_error"] is True
        assert "Couldn't reach Sonarr" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_show_status_series_parse_error(
    mock_sonarr_client: AsyncMock,
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
            {"id": 1, "status": 2, "media": {"title": "The Bear", "status": 5, "tvdbId": 123}}
        ]
    }

    series_resp = MagicMock()
    series_resp.status_code = 200
    series_resp.json.side_effect = ValueError("bad json")

    mock_overseerr_client.get.side_effect = [user_resp, req_resp]
    mock_sonarr_client.get.return_value = series_resp

    tools = build_tools(mock_sonarr_client, mock_overseerr_client, mock_identity, settings)
    get_show_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_show_status.handler({"title": "The Bear"})
        assert result["is_error"] is True
        assert "Unexpected response format from Sonarr" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_show_status_series_not_dict(
    mock_sonarr_client: AsyncMock,
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
            {"id": 1, "status": 2, "media": {"title": "The Bear", "status": 5, "tvdbId": 123}}
        ]
    }

    series_resp = MagicMock()
    series_resp.status_code = 200
    series_resp.json.return_value = ["not a dict"]

    mock_overseerr_client.get.side_effect = [user_resp, req_resp]
    mock_sonarr_client.get.return_value = series_resp

    tools = build_tools(mock_sonarr_client, mock_overseerr_client, mock_identity, settings)
    get_show_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_show_status.handler({"title": "The Bear"})
        assert result["is_error"] is True
        assert "Unexpected response format from Sonarr" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_show_status_series_missing_id(
    mock_sonarr_client: AsyncMock,
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
            {"id": 1, "status": 2, "media": {"title": "The Bear", "status": 5, "tvdbId": 123}}
        ]
    }

    series_resp = MagicMock()
    series_resp.status_code = 200
    series_resp.json.return_value = [{"title": "The Bear"}]

    mock_overseerr_client.get.side_effect = [user_resp, req_resp]
    mock_sonarr_client.get.return_value = series_resp

    tools = build_tools(mock_sonarr_client, mock_overseerr_client, mock_identity, settings)
    get_show_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_show_status.handler({"title": "The Bear"})
        assert result["is_error"] is True
        assert "Unexpected response format from Sonarr" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_get_show_status_sonarr_http_exception(
    mock_sonarr_client: AsyncMock,
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
            {"id": 1, "status": 2, "media": {"title": "The Bear", "status": 5, "tvdbId": 123}}
        ]
    }

    mock_overseerr_client.get.side_effect = [user_resp, req_resp]
    mock_sonarr_client.get.side_effect = httpx.ConnectError("connection refused")

    tools = build_tools(mock_sonarr_client, mock_overseerr_client, mock_identity, settings)
    get_show_status = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await get_show_status.handler({"title": "The Bear"})
        assert result["is_error"] is True
        assert "error occurred" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)
