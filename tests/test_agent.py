from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_agent_sdk import AssistantMessage, TextBlock
from pydantic import HttpUrl, SecretStr

from cleanrr.agent import Agent
from cleanrr.config import Settings
from cleanrr.identity import Identity
from cleanrr.tools._context import current_telegram_user_id


@pytest.fixture()
def settings_with_overseerr() -> Settings:
    return Settings(
        telegram_bot_token=SecretStr("test"),
        anthropic_api_key=SecretStr("sk-test"),
        overseerr_url=HttpUrl("http://overseerr:5055"),
        overseerr_api_key=SecretStr("ov-key"),
    )


def _collect_allowed_tools(settings: Settings, *, telegram_bot: Any = None) -> list[str]:
    """Return the allowed_tools list produced by Agent.start() for given settings."""
    captured: dict[str, object] = {}

    class _FakeSDKClient:
        def __init__(self, options: object) -> None:
            captured["options"] = options

        async def __aenter__(self) -> _FakeSDKClient:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

    async def _run() -> list[str]:
        agent = Agent(
            identity=MagicMock(spec=Identity),
            settings=settings,
            timeout_seconds=5.0,
            telegram_bot=telegram_bot,
        )
        with patch("cleanrr.agent.ClaudeSDKClient", _FakeSDKClient):
            await agent.start()
            await agent.stop()
        opts = captured["options"]
        return list(opts.allowed_tools)  # type: ignore[union-attr]

    return asyncio.run(_run())


def _collect_registered_tool_names(settings: Settings, *, telegram_bot: Any = None) -> list[str]:
    """Return names of tools registered with the MCP server (regardless of allowed_tools)."""
    captured: dict[str, object] = {}

    class _FakeSDKClient:
        def __init__(self, options: object) -> None:
            captured["options"] = options

        async def __aenter__(self) -> _FakeSDKClient:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

    def _fake_create_sdk_mcp_server(name: str, tools: list[object]) -> dict[str, object]:
        captured["tools"] = tools
        return {"type": "sdk", "name": name, "instance": MagicMock()}

    async def _run() -> list[str]:
        agent = Agent(
            identity=MagicMock(spec=Identity),
            settings=settings,
            timeout_seconds=5.0,
            telegram_bot=telegram_bot,
        )
        with (
            patch("cleanrr.agent.ClaudeSDKClient", _FakeSDKClient),
            patch("cleanrr.agent.create_sdk_mcp_server", _fake_create_sdk_mcp_server),
        ):
            await agent.start()
            await agent.stop()
        return [t.name for t in captured["tools"]]  # type: ignore[union-attr]

    return asyncio.run(_run())


@pytest.mark.asyncio
async def test_start_wires_mcp_when_overseerr_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Agent.start() builds MCP server and sets allowed_tools when Overseerr is configured."""
    from cleanrr import agent as agent_module

    captured_options: dict[str, object] = {}

    class _FakeSDKClient:
        def __init__(self, options: object) -> None:
            captured_options["options"] = options

        async def __aenter__(self) -> _FakeSDKClient:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr(agent_module, "ClaudeSDKClient", _FakeSDKClient)

    settings = Settings(
        telegram_bot_token=SecretStr("test"),
        anthropic_api_key=SecretStr("sk-test"),
        overseerr_url=HttpUrl("http://overseerr:5055"),
        overseerr_api_key=SecretStr("ov-key"),
    )
    agent = Agent(identity=MagicMock(spec=Identity), settings=settings, timeout_seconds=5.0)
    await agent.start()

    opts = captured_options["options"]
    assert "cleanrr" in opts.mcp_servers  # type: ignore[union-attr]
    assert "list_my_requests" in opts.allowed_tools  # type: ignore[union-attr]
    assert opts.tools == []  # type: ignore[union-attr]
    assert opts.permission_mode == "dontAsk"  # type: ignore[union-attr]
    assert opts.strict_mcp_config is True  # type: ignore[union-attr]

    await agent.stop()


@pytest.mark.asyncio
async def test_start_with_no_overseerr_config_registers_no_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Overseerr unconfigured, MCP server registers but no tools are allowed."""
    from cleanrr import agent as agent_module

    captured_options: dict[str, object] = {}

    class _FakeSDKClient:
        def __init__(self, options: object) -> None:
            captured_options["options"] = options

        async def __aenter__(self) -> _FakeSDKClient:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr(agent_module, "ClaudeSDKClient", _FakeSDKClient)

    settings = Settings(
        telegram_bot_token=SecretStr("test"), anthropic_api_key=SecretStr("sk-test")
    )
    agent = Agent(identity=MagicMock(spec=Identity), settings=settings, timeout_seconds=5.0)
    await agent.start()

    opts = captured_options["options"]
    assert opts.allowed_tools == []  # type: ignore[union-attr]
    assert agent.overseerr_client is None

    await agent.stop()


@pytest.mark.asyncio
async def test_start_registers_sonarr_tools_when_both_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent.start() registers get_show_status when both Sonarr and Overseerr configured."""
    from cleanrr import agent as agent_module

    captured_options: dict[str, object] = {}

    class _FakeSDKClient:
        def __init__(self, options: object) -> None:
            captured_options["options"] = options

        async def __aenter__(self) -> _FakeSDKClient:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr(agent_module, "ClaudeSDKClient", _FakeSDKClient)

    settings = Settings(
        telegram_bot_token=SecretStr("test"),
        anthropic_api_key=SecretStr("sk-test"),
        overseerr_url=HttpUrl("http://overseerr:5055"),
        overseerr_api_key=SecretStr("ov-key"),
        sonarr_url=HttpUrl("http://sonarr:8989"),
        sonarr_api_key=SecretStr("sonarr-key"),
    )
    agent = Agent(identity=MagicMock(spec=Identity), settings=settings, timeout_seconds=5.0)
    await agent.start()

    opts = captured_options["options"]
    assert "get_show_status" in opts.allowed_tools  # type: ignore[union-attr]

    await agent.stop()


@pytest.mark.asyncio
async def test_start_skips_sonarr_tools_when_overseerr_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent.start() skips Sonarr tools when Overseerr is not configured."""
    from cleanrr import agent as agent_module

    captured_options: dict[str, object] = {}

    class _FakeSDKClient:
        def __init__(self, options: object) -> None:
            captured_options["options"] = options

        async def __aenter__(self) -> _FakeSDKClient:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr(agent_module, "ClaudeSDKClient", _FakeSDKClient)

    settings = Settings(
        telegram_bot_token=SecretStr("test"),
        anthropic_api_key=SecretStr("sk-test"),
        sonarr_url=HttpUrl("http://sonarr:8989"),
        sonarr_api_key=SecretStr("sonarr-key"),
    )
    agent = Agent(identity=MagicMock(spec=Identity), settings=settings, timeout_seconds=5.0)
    await agent.start()

    opts = captured_options["options"]
    assert "get_show_status" not in opts.allowed_tools  # type: ignore[union-attr]

    await agent.stop()


@pytest.mark.asyncio
async def test_start_registers_radarr_tools_when_both_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent.start() registers get_movie_status when both Radarr and Overseerr configured."""
    from cleanrr import agent as agent_module

    captured_options: dict[str, object] = {}

    class _FakeSDKClient:
        def __init__(self, options: object) -> None:
            captured_options["options"] = options

        async def __aenter__(self) -> _FakeSDKClient:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr(agent_module, "ClaudeSDKClient", _FakeSDKClient)

    settings = Settings(
        telegram_bot_token=SecretStr("test"),
        anthropic_api_key=SecretStr("sk-test"),
        overseerr_url=HttpUrl("http://overseerr:5055"),
        overseerr_api_key=SecretStr("ov-key"),
        radarr_url=HttpUrl("http://radarr:7878"),
        radarr_api_key=SecretStr("radarr-key"),
    )
    agent = Agent(identity=MagicMock(spec=Identity), settings=settings, timeout_seconds=5.0)
    await agent.start()

    opts = captured_options["options"]
    assert "get_movie_status" in opts.allowed_tools  # type: ignore[union-attr]

    await agent.stop()


@pytest.mark.asyncio
async def test_start_skips_radarr_tools_when_overseerr_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent.start() skips Radarr tools when Overseerr is not configured."""
    from cleanrr import agent as agent_module

    captured_options: dict[str, object] = {}

    class _FakeSDKClient:
        def __init__(self, options: object) -> None:
            captured_options["options"] = options

        async def __aenter__(self) -> _FakeSDKClient:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr(agent_module, "ClaudeSDKClient", _FakeSDKClient)

    settings = Settings(
        telegram_bot_token=SecretStr("test"),
        anthropic_api_key=SecretStr("sk-test"),
        radarr_url=HttpUrl("http://radarr:7878"),
        radarr_api_key=SecretStr("radarr-key"),
    )
    agent = Agent(identity=MagicMock(spec=Identity), settings=settings, timeout_seconds=5.0)
    await agent.start()

    opts = captured_options["options"]
    assert "get_movie_status" not in opts.allowed_tools  # type: ignore[union-attr]

    await agent.stop()


def test_start_registers_qbittorrent_tools_when_all_configured(
    settings_with_overseerr: Settings,
) -> None:
    settings_with_overseerr.qbittorrent_url = HttpUrl("http://qbittorrent:8080")
    settings_with_overseerr.qbittorrent_username = "admin"
    settings_with_overseerr.qbittorrent_password = SecretStr("pass")

    allowed_tools = set(_collect_allowed_tools(settings_with_overseerr))
    assert "list_stalled_torrents" in allowed_tools


def test_start_skips_qbittorrent_tools_when_password_missing(
    settings_with_overseerr: Settings,
) -> None:
    settings_with_overseerr.qbittorrent_url = HttpUrl("http://qbittorrent:8080")
    settings_with_overseerr.qbittorrent_username = "admin"
    settings_with_overseerr.qbittorrent_password = None

    allowed_tools = set(_collect_allowed_tools(settings_with_overseerr))
    assert "list_stalled_torrents" not in allowed_tools


def _make_text_message(text: str) -> AssistantMessage:
    block = TextBlock(text=text)
    msg = MagicMock(spec=AssistantMessage)
    msg.content = [block]
    return cast(AssistantMessage, msg)


async def _slow_generator() -> AsyncIterator[AssistantMessage]:
    await asyncio.sleep(10)
    yield _make_text_message("never")


async def _fast_generator(text: str) -> AsyncIterator[AssistantMessage]:
    yield _make_text_message(text)


@pytest.mark.asyncio
async def test_respond_raises_timeout_when_sdk_hangs() -> None:
    agent = Agent(
        identity=MagicMock(spec=Identity),
        settings=Settings(
            telegram_bot_token=SecretStr("test"), anthropic_api_key=SecretStr("sk-test")
        ),
        timeout_seconds=0.1,
    )
    mock_client = AsyncMock()
    mock_client.query = AsyncMock()
    mock_client.receive_response = lambda: _slow_generator()

    agent._client = mock_client

    with pytest.raises(TimeoutError):
        await agent.respond(telegram_user_id=1, prompt="hello")


@pytest.mark.asyncio
async def test_respond_returns_normally_when_under_timeout() -> None:
    agent = Agent(
        identity=MagicMock(spec=Identity),
        settings=Settings(
            telegram_bot_token=SecretStr("test"), anthropic_api_key=SecretStr("sk-test")
        ),
        timeout_seconds=5.0,
    )
    mock_client = AsyncMock()
    mock_client.query = AsyncMock()
    mock_client.receive_response = lambda: _fast_generator("hello back")

    agent._client = mock_client

    result = await agent.respond(telegram_user_id=1, prompt="hello")

    assert result == "hello back"


@pytest.mark.asyncio
async def test_start_wires_can_use_tool_when_telegram_bot_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With telegram_bot set, can_use_tool replaces permission_mode and write tools register."""
    from cleanrr import agent as agent_module

    captured_options: dict[str, object] = {}

    class _FakeSDKClient:
        def __init__(self, options: object) -> None:
            captured_options["options"] = options

        async def __aenter__(self) -> _FakeSDKClient:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr(agent_module, "ClaudeSDKClient", _FakeSDKClient)

    settings = Settings(
        telegram_bot_token=SecretStr("test"),
        anthropic_api_key=SecretStr("sk-test"),
        overseerr_url=HttpUrl("http://overseerr:5055"),
        overseerr_api_key=SecretStr("ov-key"),
    )
    bot = MagicMock()
    agent = Agent(
        identity=MagicMock(spec=Identity),
        settings=settings,
        timeout_seconds=5.0,
        telegram_bot=bot,
    )
    await agent.start()

    opts = captured_options["options"]
    # can_use_tool replaces permission_mode in production wiring
    assert opts.can_use_tool is not None  # type: ignore[union-attr]
    assert opts.permission_mode is None  # type: ignore[union-attr]
    # Write tool must NOT be in allowed_tools — that would auto-approve it and
    # skip can_use_tool entirely, running the destructive action unconfirmed.
    assert "remove_my_request" not in opts.allowed_tools  # type: ignore[union-attr]
    assert agent.confirmation_registry is not None
    assert agent.overseerr_client is not None

    await agent.stop()
    # Registry and Overseerr client torn down on stop()
    assert agent.confirmation_registry is None
    assert agent.overseerr_client is None


def test_start_registers_write_tool_without_auto_approving_it(
    settings_with_overseerr: Settings,
) -> None:
    """remove_my_request must be available to Claude but gated behind can_use_tool."""
    registered = _collect_registered_tool_names(settings_with_overseerr, telegram_bot=MagicMock())
    assert "remove_my_request" in registered


def test_start_registers_all_write_tools_when_all_services_configured() -> None:
    """All 4 destructive tools must register (but never auto-approve) when their
    respective services + telegram_bot exist."""
    settings = Settings(
        telegram_bot_token=SecretStr("test"),
        anthropic_api_key=SecretStr("sk-test"),
        overseerr_url=HttpUrl("http://overseerr:5055"),
        overseerr_api_key=SecretStr("ov-key"),
        sonarr_url=HttpUrl("http://sonarr:8989"),
        sonarr_api_key=SecretStr("so-key"),
        radarr_url=HttpUrl("http://radarr:7878"),
        radarr_api_key=SecretStr("ra-key"),
        qbittorrent_url=HttpUrl("http://qbit:8080"),
        qbittorrent_username="admin",
        qbittorrent_password=SecretStr("pass"),
    )
    write_tools = {
        "remove_my_request",
        "delete_torrent",
        "force_research_movie",
        "force_research_show",
    }

    registered = set(_collect_registered_tool_names(settings, telegram_bot=MagicMock()))
    assert write_tools.issubset(registered), f"missing write tools: {registered}"

    allowed = set(_collect_allowed_tools(settings, telegram_bot=MagicMock()))
    assert not write_tools & allowed, f"write tools wrongly auto-approved: {write_tools & allowed}"


@pytest.mark.asyncio
async def test_start_skips_write_tools_when_no_telegram_bot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without telegram_bot, write tools cannot run safely — they must not register."""
    from cleanrr import agent as agent_module

    captured_options: dict[str, object] = {}

    class _FakeSDKClient:
        def __init__(self, options: object) -> None:
            captured_options["options"] = options

        async def __aenter__(self) -> _FakeSDKClient:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr(agent_module, "ClaudeSDKClient", _FakeSDKClient)

    settings = Settings(
        telegram_bot_token=SecretStr("test"),
        anthropic_api_key=SecretStr("sk-test"),
        overseerr_url=HttpUrl("http://overseerr:5055"),
        overseerr_api_key=SecretStr("ov-key"),
    )
    agent = Agent(
        identity=MagicMock(spec=Identity),
        settings=settings,
        timeout_seconds=5.0,
    )
    await agent.start()

    opts = captured_options["options"]
    assert "remove_my_request" not in opts.allowed_tools  # type: ignore[union-attr]
    assert opts.can_use_tool is None  # type: ignore[union-attr]
    assert opts.permission_mode == "dontAsk"  # type: ignore[union-attr]

    await agent.stop()


@pytest.mark.asyncio
async def test_lock_releases_after_timeout() -> None:
    agent = Agent(
        identity=MagicMock(spec=Identity),
        settings=Settings(
            telegram_bot_token=SecretStr("test"), anthropic_api_key=SecretStr("sk-test")
        ),
        timeout_seconds=0.1,
    )
    mock_client = AsyncMock()
    mock_client.query = AsyncMock()

    call_count = 0

    async def _slow_then_fast() -> AsyncIterator[AssistantMessage]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            await asyncio.sleep(10)
            yield _make_text_message("never")
        else:
            yield _make_text_message("second call")

    mock_client.receive_response = _slow_then_fast

    agent._client = mock_client

    with pytest.raises(TimeoutError):
        await agent.respond(telegram_user_id=1, prompt="first")

    # After the timeout the lock must be released; second call must complete quickly.
    result = await asyncio.wait_for(
        agent.respond(telegram_user_id=1, prompt="second"),
        timeout=5.0,
    )
    assert result == "second call"


@pytest.mark.asyncio
async def test_context_user_id_not_clobbered_by_concurrent_respond() -> None:
    """Regression: current_telegram_user_id must only change once a respond()
    call actually holds the lock. With concurrent_updates(True) enabled bot-
    wide, a second user's respond() starts running immediately while a first
    query is still in flight — if set() ran before lock acquisition, the
    second call would clobber the value the first query's tool calls rely on.
    """
    agent = Agent(
        identity=MagicMock(spec=Identity),
        settings=Settings(
            telegram_bot_token=SecretStr("test"), anthropic_api_key=SecretStr("sk-test")
        ),
        timeout_seconds=5.0,
    )
    mock_client = AsyncMock()
    mock_client.query = AsyncMock()

    observed_during_first: int | None = None

    async def _first_generator() -> AsyncIterator[AssistantMessage]:
        nonlocal observed_during_first
        # Real delay so the second respond() call gets scheduled and, under
        # the bug, sets current_telegram_user_id before this query finishes.
        await asyncio.sleep(0.05)
        observed_during_first = current_telegram_user_id.get()
        yield _make_text_message("first done")

    async def _second_generator() -> AsyncIterator[AssistantMessage]:
        yield _make_text_message("second done")

    call_count = 0

    def _receive_response() -> AsyncIterator[AssistantMessage]:
        nonlocal call_count
        call_count += 1
        return _first_generator() if call_count == 1 else _second_generator()

    mock_client.receive_response = _receive_response
    agent._client = mock_client

    first_task = asyncio.create_task(agent.respond(telegram_user_id=111, prompt="a"))
    await asyncio.sleep(0)
    second_task = asyncio.create_task(agent.respond(telegram_user_id=222, prompt="b"))

    first_result = await first_task
    second_result = await second_task

    assert first_result == "first done"
    assert second_result == "second done"
    assert observed_during_first == 111


@pytest.mark.asyncio
async def test_respond_reconnects_once_after_sdk_connection_error() -> None:
    """Regression: the SDK never respawns a dead CLI subprocess on its own
    (confirmed against installed claude_agent_sdk source) — without a
    reconnect here, one crash would permanently break every future message
    until someone manually restarts the process."""
    from claude_agent_sdk import CLIConnectionError

    agent = Agent(
        identity=MagicMock(spec=Identity),
        settings=Settings(
            telegram_bot_token=SecretStr("test"), anthropic_api_key=SecretStr("sk-test")
        ),
        timeout_seconds=5.0,
    )
    mock_client = AsyncMock()
    mock_client.query = AsyncMock()

    call_count = 0

    def _receive_response() -> AsyncIterator[AssistantMessage]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise CLIConnectionError("subprocess died")
        return _fast_generator("recovered")

    mock_client.receive_response = _receive_response
    agent._client = mock_client
    agent.stop = AsyncMock()  # type: ignore[method-assign]
    agent.start = AsyncMock()  # type: ignore[method-assign]

    result = await agent.respond(telegram_user_id=1, prompt="hello")

    assert result == "recovered"
    agent.stop.assert_awaited_once()
    agent.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_respond_propagates_when_reconnect_also_fails() -> None:
    """If the reconnect itself can't recover the session, the failure must
    still surface (handlers.py's generic except turns it into a graceful
    reply) rather than hanging or being swallowed."""
    from claude_agent_sdk import CLIConnectionError

    agent = Agent(
        identity=MagicMock(spec=Identity),
        settings=Settings(
            telegram_bot_token=SecretStr("test"), anthropic_api_key=SecretStr("sk-test")
        ),
        timeout_seconds=5.0,
    )
    mock_client = AsyncMock()
    mock_client.query = AsyncMock()
    mock_client.receive_response = lambda: (_ for _ in ()).throw(
        CLIConnectionError("subprocess died")
    )
    agent._client = mock_client
    agent.stop = AsyncMock()  # type: ignore[method-assign]
    agent.start = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(CLIConnectionError):
        await agent.respond(telegram_user_id=1, prompt="hello")

    agent.stop.assert_awaited_once()
    agent.start.assert_awaited_once()
