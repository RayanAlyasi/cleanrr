from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from cleanrr.config import Settings
from cleanrr.tools._context import current_telegram_user_id
from cleanrr.tools.qbittorrent import _format_age, build_tools


def _settings(**overrides: object) -> Settings:
    base = {
        "telegram_bot_token": "test_token",
        "anthropic_api_key": "sk-test",
        "_env_file": None,
        "qbittorrent_url": "http://qbittorrent:8080",
        "qbittorrent_username": "admin",
        "qbittorrent_password": "secret",
        "admin_telegram_ids": {42},
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def mock_qbit_client() -> AsyncMock:
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def settings() -> Settings:
    return _settings()


# ── Settings / gate paths ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_not_configured_url_none(mock_qbit_client: AsyncMock) -> None:
    s = _settings(qbittorrent_url=None)
    tools = build_tools(mock_qbit_client, s)
    tool = tools[0]

    result = await tool.handler({})
    assert result["is_error"] is True
    assert "qBittorrent isn't configured" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_not_configured_username_none(mock_qbit_client: AsyncMock) -> None:
    s = _settings(qbittorrent_username=None)
    tools = build_tools(mock_qbit_client, s)
    tool = tools[0]

    result = await tool.handler({})
    assert result["is_error"] is True
    assert "qBittorrent isn't configured" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_not_configured_password_none(mock_qbit_client: AsyncMock) -> None:
    s = _settings(qbittorrent_password=None)
    tools = build_tools(mock_qbit_client, s)
    tool = tools[0]

    result = await tool.handler({})
    assert result["is_error"] is True
    assert "qBittorrent isn't configured" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_context_missing(mock_qbit_client: AsyncMock, settings: Settings) -> None:
    tools = build_tools(mock_qbit_client, settings)
    tool = tools[0]

    result = await tool.handler({})
    assert result["is_error"] is True
    assert "Internal error" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_not_admin(mock_qbit_client: AsyncMock, settings: Settings) -> None:
    tools = build_tools(mock_qbit_client, settings)
    tool = tools[0]

    token = current_telegram_user_id.set(999)
    try:
        result = await tool.handler({})
        assert result["is_error"] is False
        assert "admin" in result["content"][0]["text"].lower()
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_empty_admin_ids_blocks_all(mock_qbit_client: AsyncMock) -> None:
    s = _settings(admin_telegram_ids=set())
    tools = build_tools(mock_qbit_client, s)
    tool = tools[0]

    token = current_telegram_user_id.set(42)
    try:
        result = await tool.handler({})
        assert result["is_error"] is False
        assert "admin" in result["content"][0]["text"].lower()
    finally:
        current_telegram_user_id.reset(token)


# ── Auth paths ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auth_failed_non_ok_login(mock_qbit_client: AsyncMock, settings: Settings) -> None:
    login_resp = MagicMock()
    login_resp.status_code = 403
    mock_qbit_client.post.return_value = login_resp

    tools = build_tools(mock_qbit_client, settings)
    tool = tools[0]

    token = current_telegram_user_id.set(42)
    try:
        result = await tool.handler({})
        assert result["is_error"] is True
        assert "qBittorrent auth failed" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_auth_failed_wrong_password_body(
    mock_qbit_client: AsyncMock, settings: Settings
) -> None:
    login_resp = MagicMock()
    login_resp.status_code = 200
    login_resp.text = "Fails."
    mock_qbit_client.post.return_value = login_resp

    tools = build_tools(mock_qbit_client, settings)
    tool = tools[0]

    token = current_telegram_user_id.set(42)
    try:
        result = await tool.handler({})
        assert result["is_error"] is True
        assert "qBittorrent auth failed" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_auth_failed_http_exception_on_login(
    mock_qbit_client: AsyncMock, settings: Settings
) -> None:
    mock_qbit_client.post.side_effect = httpx.ConnectError("refused")

    tools = build_tools(mock_qbit_client, settings)
    tool = tools[0]

    token = current_telegram_user_id.set(42)
    try:
        result = await tool.handler({})
        assert result["is_error"] is True
    finally:
        current_telegram_user_id.reset(token)


# ── HTTP / parse paths ────────────────────────────────────────────────────────


def _make_login_ok() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "Ok."
    return resp


@pytest.mark.asyncio
async def test_http_error_on_torrents_fetch(
    mock_qbit_client: AsyncMock, settings: Settings
) -> None:
    mock_qbit_client.post.return_value = _make_login_ok()
    mock_qbit_client.get.side_effect = httpx.ConnectError("refused")

    tools = build_tools(mock_qbit_client, settings)
    tool = tools[0]

    token = current_telegram_user_id.set(42)
    try:
        result = await tool.handler({})
        assert result["is_error"] is True
        assert "qBittorrent unreachable" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_non_200_on_torrents_fetch(mock_qbit_client: AsyncMock, settings: Settings) -> None:
    mock_qbit_client.post.return_value = _make_login_ok()
    torrent_resp = MagicMock()
    torrent_resp.status_code = 500
    mock_qbit_client.get.return_value = torrent_resp

    tools = build_tools(mock_qbit_client, settings)
    tool = tools[0]

    token = current_telegram_user_id.set(42)
    try:
        result = await tool.handler({})
        assert result["is_error"] is True
        assert "qBittorrent unreachable" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_parse_error_bad_json(mock_qbit_client: AsyncMock, settings: Settings) -> None:
    mock_qbit_client.post.return_value = _make_login_ok()
    torrent_resp = MagicMock()
    torrent_resp.status_code = 200
    torrent_resp.json.side_effect = ValueError("bad json")
    mock_qbit_client.get.return_value = torrent_resp

    tools = build_tools(mock_qbit_client, settings)
    tool = tools[0]

    token = current_telegram_user_id.set(42)
    try:
        result = await tool.handler({})
        assert result["is_error"] is True
        assert "Unexpected response" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_parse_error_not_a_list(mock_qbit_client: AsyncMock, settings: Settings) -> None:
    mock_qbit_client.post.return_value = _make_login_ok()
    torrent_resp = MagicMock()
    torrent_resp.status_code = 200
    torrent_resp.json.return_value = {"error": "unexpected"}
    mock_qbit_client.get.return_value = torrent_resp

    tools = build_tools(mock_qbit_client, settings)
    tool = tools[0]

    token = current_telegram_user_id.set(42)
    try:
        result = await tool.handler({})
        assert result["is_error"] is True
        assert "Unexpected response" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_403_triggers_retry_then_auth_fails(
    mock_qbit_client: AsyncMock, settings: Settings
) -> None:
    """GET /api/v2/torrents/info returns 403 → re-login → second login fails → auth_failed."""
    first_login = _make_login_ok()
    second_login = MagicMock()
    second_login.status_code = 403

    torrent_resp = MagicMock()
    torrent_resp.status_code = 403

    mock_qbit_client.post.side_effect = [first_login, second_login]
    mock_qbit_client.get.return_value = torrent_resp

    tools = build_tools(mock_qbit_client, settings)
    tool = tools[0]

    token = current_telegram_user_id.set(42)
    try:
        result = await tool.handler({})
        assert result["is_error"] is True
        assert "qBittorrent auth failed" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


# ── Success paths ─────────────────────────────────────────────────────────────


def _make_torrent(
    name: str = "Test.Torrent",
    state: str = "stalledDL",
    last_activity: int = 0,
    added_on: int = 0,
    size: int = 1_073_741_824,
    progress: float = 0.25,
) -> dict[str, object]:
    now = int(time.time())
    return {
        "name": name,
        "state": state,
        "last_activity": last_activity if last_activity else now - 3600,
        "added_on": added_on if added_on else now - 7200,
        "size": size,
        "progress": progress,
    }


@pytest.mark.asyncio
async def test_success_no_stalled_torrents(mock_qbit_client: AsyncMock, settings: Settings) -> None:
    mock_qbit_client.post.return_value = _make_login_ok()
    torrent_resp = MagicMock()
    torrent_resp.status_code = 200
    torrent_resp.json.return_value = [
        _make_torrent(state="downloading"),
        _make_torrent(state="seeding"),
    ]
    mock_qbit_client.get.return_value = torrent_resp

    tools = build_tools(mock_qbit_client, settings)
    tool = tools[0]

    token = current_telegram_user_id.set(42)
    try:
        result = await tool.handler({})
        assert result["is_error"] is False
        assert "No stalled" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_success_filters_to_stalled_states(
    mock_qbit_client: AsyncMock, settings: Settings
) -> None:
    mock_qbit_client.post.return_value = _make_login_ok()
    torrent_resp = MagicMock()
    torrent_resp.status_code = 200
    torrent_resp.json.return_value = [
        _make_torrent(name="Stalled One", state="stalledDL"),
        _make_torrent(name="Meta One", state="metaDL"),
        _make_torrent(name="Active One", state="downloading"),
    ]
    mock_qbit_client.get.return_value = torrent_resp

    tools = build_tools(mock_qbit_client, settings)
    tool = tools[0]

    token = current_telegram_user_id.set(42)
    try:
        result = await tool.handler({})
        text = result["content"][0]["text"]
        assert result["is_error"] is False
        assert "Stalled One" in text
        assert "Meta One" in text
        assert "Active One" not in text
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_success_caps_at_10_entries(mock_qbit_client: AsyncMock, settings: Settings) -> None:
    mock_qbit_client.post.return_value = _make_login_ok()
    torrents = [_make_torrent(name=f"Torrent {i}", state="stalledDL") for i in range(15)]
    torrent_resp = MagicMock()
    torrent_resp.status_code = 200
    torrent_resp.json.return_value = torrents
    mock_qbit_client.get.return_value = torrent_resp

    tools = build_tools(mock_qbit_client, settings)
    tool = tools[0]

    token = current_telegram_user_id.set(42)
    try:
        result = await tool.handler({})
        text = result["content"][0]["text"]
        assert result["is_error"] is False
        shown = sum(1 for i in range(15) if f"Torrent {i}" in text)
        assert shown == 10
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_success_output_includes_size_and_progress(
    mock_qbit_client: AsyncMock, settings: Settings
) -> None:
    mock_qbit_client.post.return_value = _make_login_ok()
    torrent_resp = MagicMock()
    torrent_resp.status_code = 200
    torrent_resp.json.return_value = [
        _make_torrent(
            name="Big Film",
            state="stalledDL",
            size=2_147_483_648,
            progress=0.5,
        )
    ]
    mock_qbit_client.get.return_value = torrent_resp

    tools = build_tools(mock_qbit_client, settings)
    tool = tools[0]

    token = current_telegram_user_id.set(42)
    try:
        result = await tool.handler({})
        text = result["content"][0]["text"]
        assert result["is_error"] is False
        assert "Big Film" in text
        assert "50%" in text
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_success_age_uses_last_activity(
    mock_qbit_client: AsyncMock, settings: Settings
) -> None:
    mock_qbit_client.post.return_value = _make_login_ok()
    now = int(time.time())
    torrent_resp = MagicMock()
    torrent_resp.status_code = 200
    torrent_resp.json.return_value = [
        {
            "name": "Recent Torrent",
            "state": "stalledDL",
            "last_activity": now - 120,
            "added_on": now - 86400,
            "size": 500_000_000,
            "progress": 0.1,
        }
    ]
    mock_qbit_client.get.return_value = torrent_resp

    tools = build_tools(mock_qbit_client, settings)
    tool = tools[0]

    token = current_telegram_user_id.set(42)
    try:
        result = await tool.handler({})
        text = result["content"][0]["text"]
        assert result["is_error"] is False
        assert "2m" in text
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_success_age_falls_back_to_added_on(
    mock_qbit_client: AsyncMock, settings: Settings
) -> None:
    mock_qbit_client.post.return_value = _make_login_ok()
    now = int(time.time())
    torrent_resp = MagicMock()
    torrent_resp.status_code = 200
    torrent_resp.json.return_value = [
        {
            "name": "Old Torrent",
            "state": "metaDL",
            "last_activity": 0,
            "added_on": now - 3600,
            "size": 100_000_000,
            "progress": 0.0,
        }
    ]
    mock_qbit_client.get.return_value = torrent_resp

    tools = build_tools(mock_qbit_client, settings)
    tool = tools[0]

    token = current_telegram_user_id.set(42)
    try:
        result = await tool.handler({})
        text = result["content"][0]["text"]
        assert result["is_error"] is False
        assert "1h" in text
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_success_retry_after_403_on_torrents(
    mock_qbit_client: AsyncMock, settings: Settings
) -> None:
    """GET /api/v2/torrents/info returns 403 → re-login succeeds → retry succeeds."""
    first_login = _make_login_ok()
    second_login = _make_login_ok()

    now = int(time.time())
    torrent_data = [
        {
            "name": "Stalled Film",
            "state": "stalledDL",
            "last_activity": now - 300,
            "added_on": now - 600,
            "size": 1_000_000_000,
            "progress": 0.3,
        }
    ]

    first_torrent_resp = MagicMock()
    first_torrent_resp.status_code = 403

    second_torrent_resp = MagicMock()
    second_torrent_resp.status_code = 200
    second_torrent_resp.json.return_value = torrent_data

    mock_qbit_client.post.side_effect = [first_login, second_login]
    mock_qbit_client.get.side_effect = [first_torrent_resp, second_torrent_resp]

    tools = build_tools(mock_qbit_client, settings)
    tool = tools[0]

    token = current_telegram_user_id.set(42)
    try:
        result = await tool.handler({})
        assert result["is_error"] is False
        assert "Stalled Film" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_success_empty_list(mock_qbit_client: AsyncMock, settings: Settings) -> None:
    mock_qbit_client.post.return_value = _make_login_ok()
    torrent_resp = MagicMock()
    torrent_resp.status_code = 200
    torrent_resp.json.return_value = []
    mock_qbit_client.get.return_value = torrent_resp

    tools = build_tools(mock_qbit_client, settings)
    tool = tools[0]

    token = current_telegram_user_id.set(42)
    try:
        result = await tool.handler({})
        assert result["is_error"] is False
        assert "No stalled" in result["content"][0]["text"]
    finally:
        current_telegram_user_id.reset(token)


# ── _format_age helper ────────────────────────────────────────────────────────


def test_format_age_minutes() -> None:
    now = int(time.time())
    assert _format_age(now - 90) == "1m"


def test_format_age_hours() -> None:
    now = int(time.time())
    assert _format_age(now - 7200) == "2h"


def test_format_age_days() -> None:
    now = int(time.time())
    assert _format_age(now - 86400 * 3) == "3d"


def test_format_age_zero_timestamp() -> None:
    result = _format_age(0)
    assert result == "unknown"
