from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import cleanrr.metrics
from cleanrr.config import Settings
from cleanrr.tools._context import current_telegram_user_id
from cleanrr.tools.qbittorrent_write import build_tools

_VALID_HASH = "a" * 40


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "telegram_bot_token": "test_token",
        "anthropic_api_key": "sk-test",
        "qbittorrent_url": "http://qbit:8080",
        "qbittorrent_username": "admin",
        "qbittorrent_password": "secret",
        "admin_telegram_ids": {123},
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _login_ok() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "Ok."
    return resp


def _torrent_info(name: str = "Movie.X", hash_: str = _VALID_HASH) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = [{"hash": hash_, "name": name, "size": 5_368_709_120}]
    return resp


def _empty_torrents() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = []
    return resp


def _tool_calls_value(tool: str, status: str) -> float:
    return cleanrr.metrics.tool_calls_total.labels(tool=tool, status=status)._value.get()


def _destructive_value(tool: str, outcome: str) -> float:
    return cleanrr.metrics.destructive_actions_total.labels(tool=tool, outcome=outcome)._value.get()


@pytest.fixture
def settings() -> Settings:
    return _settings()


@pytest.fixture
def mock_client() -> AsyncMock:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock()
    client.get = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_admin_happy_path_deletes_with_files(
    mock_client: AsyncMock, settings: Settings
) -> None:
    mock_client.post.side_effect = [_login_ok(), MagicMock(status_code=200)]
    mock_client.get.side_effect = [_torrent_info(), _empty_torrents()]

    tools = build_tools(mock_client, settings)
    assert len(tools) == 1
    tool_fn = tools[0]
    assert tool_fn.name == "delete_torrent"

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"torrent_hash": _VALID_HASH})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is False
    assert "Movie.X" in result["content"][0]["text"]
    # Verify deleteFiles=true was sent
    delete_call = mock_client.post.await_args_list[1]
    assert delete_call.kwargs["data"]["deleteFiles"] == "true"
    assert delete_call.kwargs["data"]["hashes"] == _VALID_HASH


@pytest.mark.asyncio
async def test_non_admin_caller_is_rejected_with_metric(
    mock_client: AsyncMock, settings: Settings
) -> None:
    tool_calls_before = _tool_calls_value("delete_torrent", "unauthorized")
    destructive_before = _destructive_value("delete_torrent", "unauthorized")

    tools = build_tools(mock_client, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(999)  # not in admin_telegram_ids
    try:
        result = await tool_fn.handler({"torrent_hash": _VALID_HASH})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True
    assert "admin" in result["content"][0]["text"].lower()
    mock_client.post.assert_not_called()
    mock_client.get.assert_not_called()
    assert _tool_calls_value("delete_torrent", "unauthorized") == tool_calls_before + 1
    # destructive_actions_total is reserved for post-confirmation outcomes —
    # admin-gate failures must not pollute its label set.
    assert _destructive_value("delete_torrent", "unauthorized") == destructive_before


@pytest.mark.asyncio
async def test_not_configured_when_qbit_url_missing(mock_client: AsyncMock) -> None:
    unconfigured = _settings(qbittorrent_url=None)
    tools = build_tools(mock_client, unconfigured)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"torrent_hash": _VALID_HASH})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True
    assert "configured" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_bad_hash_format_rejected(mock_client: AsyncMock, settings: Settings) -> None:
    tools = build_tools(mock_client, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        for bad in ["", "abc", "g" * 40, "../etc/passwd", "a" * 39]:
            result = await tool_fn.handler({"torrent_hash": bad})
            assert result["is_error"] is True, f"should reject {bad!r}"
    finally:
        current_telegram_user_id.reset(token)

    mock_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_hash_not_found_returns_friendly_message(
    mock_client: AsyncMock, settings: Settings
) -> None:
    mock_client.post.return_value = _login_ok()
    mock_client.get.return_value = _empty_torrents()

    tools = build_tools(mock_client, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"torrent_hash": _VALID_HASH})
    finally:
        current_telegram_user_id.reset(token)

    assert "no torrent" in result["content"][0]["text"].lower()
    # delete should NOT have been issued
    assert mock_client.post.await_count == 1  # only the login


@pytest.mark.asyncio
async def test_login_failure_returns_auth_error(mock_client: AsyncMock, settings: Settings) -> None:
    bad_login = MagicMock()
    bad_login.status_code = 403
    bad_login.text = "Forbidden"
    mock_client.post.return_value = bad_login

    tools = build_tools(mock_client, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"torrent_hash": _VALID_HASH})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True
    assert "auth" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_http_error_on_delete_returns_friendly(
    mock_client: AsyncMock, settings: Settings
) -> None:
    mock_client.post.side_effect = [_login_ok(), httpx.RequestError("boom")]
    mock_client.get.return_value = _torrent_info()

    tools = build_tools(mock_client, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"torrent_hash": _VALID_HASH})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True


@pytest.mark.asyncio
async def test_delete_non_200_returns_error(mock_client: AsyncMock, settings: Settings) -> None:
    bad_del = MagicMock()
    bad_del.status_code = 500
    mock_client.post.side_effect = [_login_ok(), bad_del]
    mock_client.get.return_value = _torrent_info()

    tools = build_tools(mock_client, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"torrent_hash": _VALID_HASH})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True
    assert "500" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_delete_accepted_but_torrent_still_present_returns_error(
    mock_client: AsyncMock, settings: Settings
) -> None:
    mock_client.post.side_effect = [_login_ok(), MagicMock(status_code=200)]
    # pre-delete shows torrent; post-delete still shows it → suspicious
    mock_client.get.side_effect = [_torrent_info(), _torrent_info()]

    tools = build_tools(mock_client, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"torrent_hash": _VALID_HASH})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True
    assert "still listed" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_session_expired_during_torrent_fetch(
    mock_client: AsyncMock, settings: Settings
) -> None:
    forbidden = MagicMock()
    forbidden.status_code = 403
    mock_client.post.return_value = _login_ok()
    mock_client.get.return_value = forbidden

    tools = build_tools(mock_client, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(123)
    try:
        result = await tool_fn.handler({"torrent_hash": _VALID_HASH})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is True
    assert "session" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_missing_contextvar_returns_internal_error(
    mock_client: AsyncMock, settings: Settings
) -> None:
    tools = build_tools(mock_client, settings)
    tool_fn = tools[0]
    # No contextvar set
    result = await tool_fn.handler({"torrent_hash": _VALID_HASH})
    assert result["is_error"] is True
    assert "internal error" in result["content"][0]["text"].lower()
