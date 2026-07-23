from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from cleanrr.config import Settings
from cleanrr.identity import Identity
from cleanrr.tools._context import current_telegram_user_id
from cleanrr.tools._status_label import _format_status_label
from cleanrr.tools._user_request import _resolve_user_id
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


# ---------------------------------------------------------------------------
# Helper tests
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
async def test_resolve_user_id_404(mock_client: AsyncMock) -> None:
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
async def test_resolve_user_id_500(mock_client: AsyncMock) -> None:
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


@pytest.mark.parametrize(
    ("req_status", "media_status", "expected"),
    [
        (1, None, "pending"),
        (2, 3, "approved, processing"),
        (3, None, "declined"),
        (2, 5, "approved, available"),
        (None, None, "unknown"),
        (1, 2, "pending, pending download"),
        (2, 4, "approved, partially available"),
        (2, 2, "approved, pending download"),
        (3, 5, "declined, available"),
        (None, 3, "processing"),
    ],
)
def test_format_status_label_combinations(
    req_status: int | None, media_status: int | None, expected: str
) -> None:
    assert _format_status_label(req_status, media_status) == expected


# ---------------------------------------------------------------------------
# list_my_requests tests (unchanged from before helper extraction)
# ---------------------------------------------------------------------------


def test_list_my_requests_has_no_input_schema(
    mock_identity: MagicMock, mock_client: AsyncMock
) -> None:
    """The tool takes no arguments — it always returns the caller's full list."""
    tools = build_tools(mock_client, mock_identity, _settings())
    tool_fn = tools[0]
    assert tool_fn.name == "list_my_requests"
    assert tool_fn.input_schema == {}


@pytest.mark.asyncio
async def test_list_my_requests_not_configured(
    mock_identity: MagicMock, mock_client: AsyncMock
) -> None:
    """Tool returns 'not configured' when settings are missing."""
    unconfigured = _settings(overseerr_url=None, overseerr_api_key=None)
    tools = build_tools(mock_client, mock_identity, unconfigured)
    assert len(tools) == 2

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


@pytest.mark.asyncio
async def test_list_my_requests_resolves_titles_from_real_overseerr_shape(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    """Real Overseerr requests carry only tmdbId, never a title/name — the tool
    must resolve it via /movie or /tv before it can display anything useful."""
    mock_identity.get_link = AsyncMock(return_value="testuser")

    user_response = MagicMock()
    user_response.status_code = 200
    user_response.json.return_value = {"results": [{"id": 123}]}

    requests_response = MagicMock()
    requests_response.status_code = 200
    requests_response.json.return_value = {
        "results": [
            {"id": 1, "status": 2, "media": {"mediaType": "movie", "tmdbId": 194, "status": 5}},
        ]
    }

    movie_detail_response = MagicMock()
    movie_detail_response.status_code = 200
    movie_detail_response.json.return_value = {"id": 194, "title": "Amélie"}

    mock_client.get.side_effect = [user_response, requests_response, movie_detail_response]

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = tools[0]

    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({})
    finally:
        current_telegram_user_id.reset(token)

    assert result["is_error"] is False
    assert "Amélie" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# find_my_request integration tests
# ---------------------------------------------------------------------------


def _find_tool(tools: list) -> object:  # type: ignore[type-arg]
    for t in tools:
        if t.name == "find_my_request":
            return t
    raise AssertionError("find_my_request tool not found")


def _make_requests_payload(*titles: str, req_status: int = 2, media_status: int = 5) -> dict:  # type: ignore[type-arg]
    results = []
    for title in titles:
        results.append(
            {
                "id": len(results) + 1,
                "status": req_status,
                "media": {"title": title, "releaseYear": 2024, "status": media_status},
            }
        )
    return {"results": results}


@pytest.mark.asyncio
async def test_find_request_not_configured(
    mock_identity: MagicMock, mock_client: AsyncMock
) -> None:
    unconfigured = _settings(overseerr_url=None, overseerr_api_key=None)
    tools = build_tools(mock_client, mock_identity, unconfigured)
    tool_fn = _find_tool(tools)

    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({"title": "Dune"})  # type: ignore[union-attr]
        assert result["is_error"] is True
        assert "configured" in result["content"][0]["text"].lower()
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_find_request_unlinked_user(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value=None)
    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = _find_tool(tools)

    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({"title": "Dune"})  # type: ignore[union-attr]
        assert result["is_error"] is False
        assert "linked" in result["content"][0]["text"].lower()
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_find_request_empty_input(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="testuser")
    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = _find_tool(tools)

    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({"title": "   "})  # type: ignore[union-attr]
        assert result["is_error"] is False
        assert "which title" in result["content"][0]["text"].lower()
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_find_request_context_missing(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    """Tool returns 'Internal error' when ContextVar is not set."""
    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = _find_tool(tools)

    result = await tool_fn.handler({"title": "Dune"})  # type: ignore[union-attr]
    assert result["is_error"] is True
    assert "internal error" in result["content"][0]["text"].lower()
    assert mock_client.get.call_count == 0


@pytest.mark.asyncio
async def test_find_request_user_not_found(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    """When user search returns 404, returns user_not_found error."""
    mock_identity.get_link = AsyncMock(return_value="testuser")
    resp = MagicMock()
    resp.status_code = 404
    mock_client.get.return_value = resp

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = _find_tool(tools)

    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({"title": "Dune"})  # type: ignore[union-attr]
        assert result["is_error"] is False
        assert "couldn't find your overseerr account" in result["content"][0]["text"].lower()
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_find_request_user_parse_error(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    """When user search returns malformed JSON, returns parse_error."""
    mock_identity.get_link = AsyncMock(return_value="testuser")
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("bad json")
    mock_client.get.return_value = resp

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = _find_tool(tools)

    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({"title": "Dune"})  # type: ignore[union-attr]
        assert result["is_error"] is True
        assert "unexpected response format" in result["content"][0]["text"].lower()
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_find_request_user_http_error(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    """When user search returns 500, returns http_error."""
    mock_identity.get_link = AsyncMock(return_value="testuser")
    resp = MagicMock()
    resp.status_code = 500
    mock_client.get.return_value = resp

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = _find_tool(tools)

    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({"title": "Dune"})  # type: ignore[union-attr]
        assert result["is_error"] is True
        assert "couldn't reach" in result["content"][0]["text"].lower()
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_find_request_no_match(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="testuser")

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {"results": [{"id": 99}]}

    req_resp = MagicMock()
    req_resp.status_code = 200
    req_resp.json.return_value = _make_requests_payload("Dune Part One", "Dune Part Two")

    mock_client.get.side_effect = [user_resp, req_resp]

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = _find_tool(tools)

    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({"title": "X-Files"})  # type: ignore[union-attr]
        assert result["is_error"] is False
        text = result["content"][0]["text"]
        assert "couldn't find" in text.lower()
        assert "X-Files" in text
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_find_request_exact_match(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="testuser")

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {"results": [{"id": 99}]}

    req_resp = MagicMock()
    req_resp.status_code = 200
    req_resp.json.return_value = _make_requests_payload(
        "Dune Part One", req_status=2, media_status=5
    )

    mock_client.get.side_effect = [user_resp, req_resp]

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = _find_tool(tools)

    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({"title": "dune part one"})  # type: ignore[union-attr]
        assert result["is_error"] is False
        text = result["content"][0]["text"]
        assert "Dune Part One" in text
        assert "available" in text
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_find_request_fuzzy_match(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="testuser")

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {"results": [{"id": 99}]}

    req_resp = MagicMock()
    req_resp.status_code = 200
    req_resp.json.return_value = _make_requests_payload(
        "Dune Part Two", req_status=2, media_status=3
    )

    mock_client.get.side_effect = [user_resp, req_resp]

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = _find_tool(tools)

    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({"title": "dune part 2"})  # type: ignore[union-attr]
        assert result["is_error"] is False
        text = result["content"][0]["text"]
        assert "Dune Part Two" in text
        assert "processing" in text
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_find_request_year_stripped(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="testuser")

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {"results": [{"id": 99}]}

    req_resp = MagicMock()
    req_resp.status_code = 200
    req_resp.json.return_value = {
        "results": [
            {
                "id": 1,
                "status": 2,
                "media": {"title": "Dune Part Two", "releaseYear": 2024, "status": 5},
            }
        ]
    }

    mock_client.get.side_effect = [user_resp, req_resp]

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = _find_tool(tools)

    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({"title": "Dune Part Two (2024)"})  # type: ignore[union-attr]
        assert result["is_error"] is False
        text = result["content"][0]["text"]
        assert "Dune Part Two" in text
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_find_request_multi_match(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="testuser")

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {"results": [{"id": 99}]}

    req_resp = MagicMock()
    req_resp.status_code = 200
    req_resp.json.return_value = _make_requests_payload("Dune Part One", "Dune Part Two")

    mock_client.get.side_effect = [user_resp, req_resp]

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = _find_tool(tools)

    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({"title": "Dune"})  # type: ignore[union-attr]
        assert result["is_error"] is False
        text = result["content"][0]["text"]
        assert "possible matches" in text.lower()
        assert "Dune Part One" in text
        assert "Dune Part Two" in text
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_find_request_http_error(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    mock_identity.get_link = AsyncMock(return_value="testuser")

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {"results": [{"id": 99}]}

    req_resp = MagicMock()
    req_resp.status_code = 500

    mock_client.get.side_effect = [user_resp, req_resp]

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = _find_tool(tools)

    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({"title": "Dune"})  # type: ignore[union-attr]
        assert result["is_error"] is True
        assert "couldn't reach" in result["content"][0]["text"].lower()
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
async def test_find_request_requests_parse_error(
    mock_identity: MagicMock, mock_client: AsyncMock, settings: Settings
) -> None:
    """When requests fetch returns malformed JSON, returns parse_error."""
    mock_identity.get_link = AsyncMock(return_value="testuser")

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {"results": [{"id": 99}]}

    req_resp = MagicMock()
    req_resp.status_code = 200
    req_resp.json.side_effect = ValueError("bad json")

    mock_client.get.side_effect = [user_resp, req_resp]

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = _find_tool(tools)

    token = current_telegram_user_id.set(1)
    try:
        result = await tool_fn.handler({"title": "Dune"})  # type: ignore[union-attr]
        assert result["is_error"] is True
        assert "unexpected response format" in result["content"][0]["text"].lower()
    finally:
        current_telegram_user_id.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("title", "mock_setup", "expected_status"),
    [
        ("", None, "empty_input"),
        ("Dune", "no_match", "no_match"),
        ("Dune", "multi_match", "multi_match"),
        ("Dune Part One", "single_match", "success"),
    ],
)
async def test_find_request_increments_metric_on_every_exit(
    mock_identity: MagicMock,
    mock_client: AsyncMock,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    title: str,
    mock_setup: str | None,
    expected_status: str,
) -> None:
    import cleanrr.metrics as metrics_module

    recorded: list[tuple[str, str]] = []

    class _FakeCounter:
        def labels(self, tool: str, status: str) -> _FakeCounter:
            recorded.append((tool, status))
            return self

        def inc(self) -> None:
            pass

    monkeypatch.setattr(metrics_module, "tool_calls_total", _FakeCounter())

    mock_identity.get_link = AsyncMock(return_value="testuser")

    if mock_setup == "no_match":
        user_resp = MagicMock()
        user_resp.status_code = 200
        user_resp.json.return_value = {"results": [{"id": 99}]}
        req_resp = MagicMock()
        req_resp.status_code = 200
        req_resp.json.return_value = _make_requests_payload("Something Else Entirely")
        mock_client.get.side_effect = [user_resp, req_resp]
    elif mock_setup == "multi_match":
        user_resp = MagicMock()
        user_resp.status_code = 200
        user_resp.json.return_value = {"results": [{"id": 99}]}
        req_resp = MagicMock()
        req_resp.status_code = 200
        req_resp.json.return_value = _make_requests_payload("Dune Part One", "Dune Part Two")
        mock_client.get.side_effect = [user_resp, req_resp]
    elif mock_setup == "single_match":
        user_resp = MagicMock()
        user_resp.status_code = 200
        user_resp.json.return_value = {"results": [{"id": 99}]}
        req_resp = MagicMock()
        req_resp.status_code = 200
        req_resp.json.return_value = _make_requests_payload("Dune Part One")
        mock_client.get.side_effect = [user_resp, req_resp]

    tools = build_tools(mock_client, mock_identity, settings)
    tool_fn = _find_tool(tools)

    token = current_telegram_user_id.set(1)
    try:
        await tool_fn.handler({"title": title})  # type: ignore[union-attr]
    finally:
        current_telegram_user_id.reset(token)

    matched = any(t == "find_my_request" and s == expected_status for t, s in recorded)
    assert matched, f"Expected metric find_my_request/{expected_status}, got {recorded}"
