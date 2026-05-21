from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from claude_agent_sdk import AssistantMessage, TextBlock
from pydantic import SecretStr

from cleanrr.agent import Agent
from cleanrr.config import Settings
from cleanrr.identity import Identity


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
        settings=Settings(telegram_bot_token=SecretStr("test"), anthropic_api_key="sk-test"),
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
        settings=Settings(telegram_bot_token=SecretStr("test"), anthropic_api_key="sk-test"),
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
        settings=Settings(telegram_bot_token=SecretStr("test"), anthropic_api_key="sk-test"),
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
