from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import SecretStr

from cleanrr.config import Settings
from cleanrr.tools._qbittorrent_auth import (
    QbitAuthError,
    fetch_torrents,
    login,
    normalize_torrent_hash,
)


def _settings(**overrides: object) -> Settings:
    base = {
        "telegram_bot_token": "test_token",
        "anthropic_api_key": "sk-test",
        "_env_file": None,
        "qbittorrent_username": "admin",
        "qbittorrent_password": SecretStr("secret"),
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# normalize_torrent_hash
# ---------------------------------------------------------------------------


def test_normalize_torrent_hash_valid_lowercase() -> None:
    h = "a" * 40
    assert normalize_torrent_hash(h) == h


def test_normalize_torrent_hash_strips_and_lowercases() -> None:
    h = "A" * 40
    assert normalize_torrent_hash(f"  {h}  ") == "a" * 40


def test_normalize_torrent_hash_rejects_wrong_length() -> None:
    assert normalize_torrent_hash("a" * 39) is None
    assert normalize_torrent_hash("a" * 41) is None


def test_normalize_torrent_hash_rejects_non_hex_chars() -> None:
    assert normalize_torrent_hash("g" * 40) is None


def test_normalize_torrent_hash_rejects_non_string() -> None:
    assert normalize_torrent_hash(None) is None
    assert normalize_torrent_hash(12345) is None


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_no_password_configured() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    settings = _settings(qbittorrent_password=None)

    with pytest.raises(QbitAuthError, match="no password"):
        await login(client, "http://qbittorrent:8080", settings)

    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_login_success_204() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    resp = MagicMock()
    resp.status_code = 204
    resp.text = ""
    client.post.return_value = resp

    await login(client, "http://qbittorrent:8080", _settings())  # must not raise


@pytest.mark.asyncio
async def test_login_success_200_ok() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "Ok."
    client.post.return_value = resp

    await login(client, "http://qbittorrent:8080", _settings())  # must not raise


@pytest.mark.asyncio
async def test_login_rejects_401() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    resp = MagicMock()
    resp.status_code = 401
    resp.text = "Unauthorized"
    client.post.return_value = resp

    with pytest.raises(QbitAuthError, match="401"):
        await login(client, "http://qbittorrent:8080", _settings())


@pytest.mark.asyncio
async def test_login_wraps_http_error() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.side_effect = httpx.ConnectError("boom")

    with pytest.raises(QbitAuthError):
        await login(client, "http://qbittorrent:8080", _settings())


# ---------------------------------------------------------------------------
# fetch_torrents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_torrents_success() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = [{"name": "Torrent A"}]
    client.get.return_value = resp

    torrents, needs_reauth = await fetch_torrents(client, "http://qbittorrent:8080")

    assert torrents == [{"name": "Torrent A"}]
    assert needs_reauth is False


@pytest.mark.asyncio
async def test_fetch_torrents_403_signals_reauth() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    resp = MagicMock()
    resp.status_code = 403
    client.get.return_value = resp

    torrents, needs_reauth = await fetch_torrents(client, "http://qbittorrent:8080")

    assert torrents == []
    assert needs_reauth is True


@pytest.mark.asyncio
async def test_fetch_torrents_passes_hashes_filter() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = []
    client.get.return_value = resp

    await fetch_torrents(client, "http://qbittorrent:8080", hashes="abc123")

    client.get.assert_awaited_once_with(
        "http://qbittorrent:8080/api/v2/torrents/info", params={"hashes": "abc123"}
    )


@pytest.mark.asyncio
async def test_fetch_torrents_raises_on_unexpected_status() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    resp = MagicMock()
    resp.status_code = 500
    resp.request = MagicMock()
    client.get.return_value = resp

    with pytest.raises(httpx.HTTPStatusError):
        await fetch_torrents(client, "http://qbittorrent:8080")


@pytest.mark.asyncio
async def test_fetch_torrents_raises_valueerror_on_malformed_json() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("bad json")
    client.get.return_value = resp

    with pytest.raises(ValueError, match="parse_error"):
        await fetch_torrents(client, "http://qbittorrent:8080")


@pytest.mark.asyncio
async def test_fetch_torrents_raises_valueerror_on_non_list() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"not": "a list"}
    client.get.return_value = resp

    with pytest.raises(ValueError, match="parse_error"):
        await fetch_torrents(client, "http://qbittorrent:8080")
