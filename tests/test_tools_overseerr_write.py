from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import cleanrr.metrics
from cleanrr.config import Settings
from cleanrr.identity import Identity
from cleanrr.tools._context import current_telegram_user_id
from cleanrr.tools.overseerr_write import build_tools


def _settings(**overrides: object) -> Settings:
    base = {
        "telegram_bot_token": "test_token",
        "anthropic_api_key": "sk-test",
        "_env_file": None,
        "overseerr_url": "http://overseerr:5055",
        "overseerr_api_key": "test_key",
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
    return _settings()


def _user_search_response(user_id: int = 42) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"results": [{"id": user_id}]}
    return resp


def _request_get_response(
    *, status_code: int = 200, owner_id: int = 42, title: str = "Dune"
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {
        "id": 7,
        "status": 1,
        "requestedBy": {"id": owner_id},
        "media": {"title": title, "mediaType": "movie"},
    }
    return resp


def _delete_response(status_code: int = 204) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    return resp


def _tool_calls_value(tool: str, status: str) -> float:
    return cleanrr.metrics.tool_calls_total.labels(tool=tool, status=status)._value.get()


def _destructive_value(tool: str, outcome: str) -> float:
    return cleanrr.metrics.destructive_actions_total.labels(tool=tool, outcome=outcome)._value.get()


@pytest.mark.asyncio
async def test_happy_path_deletes_owned_request(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")
    mock_client.get.side_effect = [
        _user_search_response(user_id=42),
        _request_get_response(owner_id=42, title="Dune"),
    ]
    mock_client.delete.return_value = _delete_response(204)

    tools = build_tools(mock_client, mock_identity, settings)
    assert len(tools) == 1
    tool_fn = tools[0]
    assert tool_fn.name == "remove_my_request"

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"request_id": 7})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is False
    assert "Dune" in result["content"][0]["text"]
    mock_client.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_unlinked_user_returns_error_without_http_calls(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value=None)

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"request_id": 7})
    finally:
        current_telegram_user_id.reset(token)

    assert "link" in result["content"][0]["text"].lower()
    mock_client.get.assert_not_called()
    mock_client.delete.assert_not_called()


@pytest.mark.asyncio
async def test_ownership_mismatch_increments_unauthorized_metric_and_skips_delete(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    tool_calls_before = _tool_calls_value("remove_my_request", "unauthorized")
    destructive_before = _destructive_value("remove_my_request", "unauthorized")

    mock_identity.get_link = AsyncMock(return_value="alice")
    mock_client.get.side_effect = [
        _user_search_response(user_id=42),
        _request_get_response(owner_id=99, title="Someone else's"),
    ]

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"request_id": 7})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True
    assert "not your" in result["content"][0]["text"].lower()
    mock_client.delete.assert_not_called()
    assert _tool_calls_value("remove_my_request", "unauthorized") == tool_calls_before + 1
    # destructive_actions_total is reserved for post-confirmation outcomes —
    # ownership failures must not pollute its label set.
    assert _destructive_value("remove_my_request", "unauthorized") == destructive_before


@pytest.mark.asyncio
async def test_get_404_is_idempotent_success(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")
    mock_client.get.side_effect = [
        _user_search_response(user_id=42),
        _request_get_response(status_code=404),
    ]

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"request_id": 7})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is False
    assert "already removed" in result["content"][0]["text"].lower()
    mock_client.delete.assert_not_called()


@pytest.mark.asyncio
async def test_delete_404_is_idempotent_success(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")
    mock_client.get.side_effect = [
        _user_search_response(user_id=42),
        _request_get_response(owner_id=42),
    ]
    mock_client.delete.return_value = _delete_response(404)

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"request_id": 7})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is False
    assert "already removed" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_delete_500_returns_error(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")
    mock_client.get.side_effect = [
        _user_search_response(user_id=42),
        _request_get_response(owner_id=42),
    ]
    mock_client.delete.return_value = _delete_response(500)

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"request_id": 7})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True
    assert "500" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_not_configured_when_overseerr_missing(
    mock_identity: MagicMock, mock_client: AsyncMock
) -> None:
    unconfigured = _settings(overseerr_url=None, overseerr_api_key=None)
    tools = build_tools(mock_client, mock_identity, unconfigured)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"request_id": 7})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True
    assert "configured" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_bad_request_id_returns_error(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"request_id": -1})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True
    mock_client.get.assert_not_called()


@pytest.mark.asyncio
async def test_user_resolve_404_returns_friendly_error(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")
    user_search_404 = MagicMock()
    user_search_404.status_code = 404
    mock_client.get.return_value = user_search_404

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"request_id": 7})
    finally:
        current_telegram_user_id.reset(token)

    assert "couldn't find" in result["content"][0]["text"].lower()
    mock_client.delete.assert_not_called()


@pytest.mark.asyncio
async def test_user_resolve_5xx_returns_error(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")
    user_search_500 = MagicMock()
    user_search_500.status_code = 500
    mock_client.get.return_value = user_search_500

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"request_id": 7})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True
    mock_client.delete.assert_not_called()


@pytest.mark.asyncio
async def test_get_http_error_returns_friendly_message(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")
    mock_client.get.side_effect = [
        _user_search_response(user_id=42),
        httpx.RequestError("boom"),
    ]

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"request_id": 7})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True
    mock_client.delete.assert_not_called()


@pytest.mark.asyncio
async def test_get_5xx_returns_error(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")
    bad_resp = MagicMock()
    bad_resp.status_code = 500
    mock_client.get.side_effect = [_user_search_response(user_id=42), bad_resp]

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"request_id": 7})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True
    assert "500" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_malformed_json_returns_error(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")
    bad_json = MagicMock()
    bad_json.status_code = 200
    bad_json.json.side_effect = ValueError("bad json")
    mock_client.get.side_effect = [_user_search_response(user_id=42), bad_json]

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"request_id": 7})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True
    assert "format" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_delete_http_error_returns_friendly_message(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="alice")
    mock_client.get.side_effect = [
        _user_search_response(user_id=42),
        _request_get_response(owner_id=42),
    ]
    mock_client.delete.side_effect = httpx.RequestError("boom")

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"request_id": 7})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True


@pytest.mark.asyncio
async def test_missing_contextvar_returns_internal_error(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]
    # Don't set the contextvar — simulate the tool firing outside a request scope.
    result = await tool_fn.handler({"request_id": 7})

    assert result["is_error"] is True
    assert "internal error" in result["content"][0]["text"].lower()
    mock_client.get.assert_not_called()
