from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from cleanrr.config import Settings
from cleanrr.identity import Identity
from cleanrr.tools._context import current_telegram_user_id
from cleanrr.tools._user_request import _resolve_user_id, find_user_request


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
