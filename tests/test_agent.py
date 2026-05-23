from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from claude_agent_sdk import AssistantMessage, TextBlock
from pydantic import HttpUrl, SecretStr

from cleanrr.agent import Agent
from cleanrr.config import Settings
from cleanrr.identity import Identity


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
