from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from cleanrr.config import Settings
from cleanrr.identity import Identity
from cleanrr.tools._context import current_telegram_user_id
from cleanrr.tools.overseerr import build_tools


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
        overseerr_timeout_seconds=10.0,
    )


@pytest.mark.asyncio
async def test_list_my_requests_not_configured(
    mock_identity: MagicMock, mock_client: AsyncMock
) -> None:
    """Tool returns 'not configured' when settings are missing."""
    unconfigured = _settings(overseerr_url=None, overseerr_api_key=None)
    tools = build_tools(mock_client, mock_identity, unconfigured)
    assert len(tools) == 1

    tool_fn = tools[0]
    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({})
        assert result["is_error"] is True
        assert "configured" in result["content"][0]["text"].lower()
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_list_my_requests_unlinked_user(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    """Tool returns 'you haven't linked' when user has no mapping."""
    mock_identity.get_link = AsyncMock(return_value=None)
    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({})
        assert result["is_error"] is False
        assert "linked" in result["content"][0]["text"].lower()
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_list_my_requests_user_search_404(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    """Tool handles 404 on user search gracefully."""
    mock_identity.get_link = AsyncMock(return_value="testuser")
    user_response = MagicMock()
    user_response.status_code = 404
    mock_client.get.return_value = user_response

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({})
        assert result["is_error"] is False
        assert "couldn't find" in result["content"][0]["text"].lower()
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_list_my_requests_user_search_500(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    """Tool handles HTTP 500 on user search."""
    mock_identity.get_link = AsyncMock(return_value="testuser")
    user_response = MagicMock()
    user_response.status_code = 500
    mock_client.get.return_value = user_response

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({})
        assert result["is_error"] is True
        assert "couldn't reach" in result["content"][0]["text"].lower()
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_list_my_requests_empty(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    """Tool returns 'nothing requested' when user has zero requests."""
    mock_identity.get_link = AsyncMock(return_value="testuser")

    user_response = MagicMock()
    user_response.status_code = 200
    user_response.json.return_value = {"results": [{"id": 123}]}

    requests_response = MagicMock()
    requests_response.status_code = 200
    requests_response.json.return_value = {"results": []}

    mock_client.get.side_effect = [user_response, requests_response]

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({})
        assert result["is_error"] is False
        assert "haven't requested anything" in result["content"][0]["text"].lower()
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_list_my_requests_context_missing(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    """Tool returns 'Internal error' when ContextVar is not set."""
    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]

    result = await tool_fn.handler({})
    assert result["is_error"] is True
    assert "internal error" in result["content"][0]["text"].lower()
    assert mock_client.get.call_count == 0


@pytest.mark.asyncio
async def test_list_my_requests_parse_error(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    """Tool returns 'Unexpected response format' when json() raises ValueError."""
    mock_identity.get_link = AsyncMock(return_value="testuser")

    user_response = MagicMock()
    user_response.status_code = 200
    user_response.json.side_effect = ValueError("bad json")
    mock_client.get.return_value = user_response

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({})
        assert result["is_error"] is True
        assert "unexpected response format" in result["content"][0]["text"].lower()
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_list_my_requests_user_search_empty_results(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    """200 OK with empty results list → user_not_found."""
    mock_identity.get_link = AsyncMock(return_value="testuser")
    user_response = MagicMock()
    user_response.status_code = 200
    user_response.json.return_value = {"results": []}
    mock_client.get.return_value = user_response

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]
    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({})
        assert result["is_error"] is False
        assert "couldn't find" in result["content"][0]["text"].lower()
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_list_my_requests_requests_http_500(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    """HTTP 500 on requests fetch → http_error."""
    mock_identity.get_link = AsyncMock(return_value="testuser")
    user_response = MagicMock()
    user_response.status_code = 200
    user_response.json.return_value = {"results": [{"id": 123}]}
    requests_response = MagicMock()
    requests_response.status_code = 500
    mock_client.get.side_effect = [user_response, requests_response]

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]
    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({})
        assert result["is_error"] is True
        assert "couldn't fetch" in result["content"][0]["text"].lower()
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_list_my_requests_requests_parse_error(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    """Malformed requests JSON → parse_error."""
    mock_identity.get_link = AsyncMock(return_value="testuser")
    user_response = MagicMock()
    user_response.status_code = 200
    user_response.json.return_value = {"results": [{"id": 123}]}
    requests_response = MagicMock()
    requests_response.status_code = 200
    requests_response.json.side_effect = ValueError("malformed")
    mock_client.get.side_effect = [user_response, requests_response]

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]
    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({})
        assert result["is_error"] is True
        assert "unexpected response format" in result["content"][0]["text"].lower()
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_list_my_requests_formats_declined_and_partial(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    """Status formatting covers declined (req=3) and partially_available (media=4)."""
    mock_identity.get_link = AsyncMock(return_value="testuser")
    user_response = MagicMock()
    user_response.status_code = 200
    user_response.json.return_value = {"results": [{"id": 123}]}
    requests_response = MagicMock()
    requests_response.status_code = 200
    requests_response.json.return_value = {
        "results": [
            {"id": 1, "status": 3, "media": {"title": "Bad Movie", "status": 4}},
        ]
    }
    mock_client.get.side_effect = [user_response, requests_response]

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]
    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({})
        text = result["content"][0]["text"]
        assert "declined" in text
        assert "partially available" in text
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_list_my_requests_unexpected_exception(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    """Unexpected exception in HTTP call → outer catch, http_error metric."""
    mock_identity.get_link = AsyncMock(return_value="testuser")
    mock_client.get.side_effect = RuntimeError("boom")

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]
    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({})
        assert result["is_error"] is True
        assert "error" in result["content"][0]["text"].lower()
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_list_my_requests_formatted_output(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    """Tool formats requests with titles, years, and statuses correctly."""
    mock_identity.get_link = AsyncMock(return_value="testuser")

    user_response = MagicMock()
    user_response.status_code = 200
    user_response.json.return_value = {"results": [{"id": 123}]}

    requests_response = MagicMock()
    requests_response.status_code = 200
    requests_response.json.return_value = {
        "results": [
            {
                "id": 1,
                "status": 2,  # approved
                "media": {
                    "title": "The Matrix",
                    "releaseYear": 1999,
                    "status": 5,  # available
                },
            },
            {
                "id": 2,
                "status": 1,  # pending
                "media": {"name": "Breaking Bad", "status": 3},  # processing
            },
        ]
    }

    mock_client.get.side_effect = [user_response, requests_response]

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({})
        assert result["is_error"] is False
        text = result["content"][0]["text"]
        assert "2 Overseerr request" in text
        assert "The Matrix (1999)" in text
        assert "available" in text
        assert "Breaking Bad" in text
        assert "processing" in text
    finally:
        current_telegram_user_id.reset(token)
