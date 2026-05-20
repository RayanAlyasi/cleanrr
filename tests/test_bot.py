from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cleanrr import metrics
from cleanrr.bot import (
    AGENT_KEY,
    IDENTITY_KEY,
    SETTINGS_KEY,
    _on_shutdown,
    configure_logging,
    on_message,
)
from cleanrr.config import Settings


def _make_settings(max_chars: int = 2000, timeout: float = 30.0) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="fake-bot-token",  # type: ignore[arg-type]
        anthropic_api_key="sk-fake",  # type: ignore[arg-type]
        telegram_max_message_chars=max_chars,
        claude_timeout_seconds=timeout,
    )


def _make_update(text: str, user_id: int = 1) -> MagicMock:
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = "alice"
    return update


def _make_context(agent: MagicMock, settings: Settings) -> MagicMock:
    context = MagicMock()
    context.application.bot_data = {AGENT_KEY: agent, SETTINGS_KEY: settings}
    return context


def _counter_value(label_values: dict[str, str]) -> float:
    return metrics.claude_requests_total.labels(**label_values)._value.get()


@pytest.mark.asyncio
async def test_on_message_rejects_overlong_message() -> None:
    settings = _make_settings(max_chars=10)
    agent = MagicMock()
    agent.respond = AsyncMock()
    update = _make_update("x" * 11)
    context = _make_context(agent, settings)

    before = _counter_value({"status": "rejected_too_long"})

    await on_message(update, context)

    agent.respond.assert_not_called()
    update.message.reply_text.assert_awaited_once()
    reply = update.message.reply_text.await_args.args[0]
    assert "10-char limit" in reply
    assert _counter_value({"status": "rejected_too_long"}) == before + 1


@pytest.mark.asyncio
async def test_on_message_handles_timeout() -> None:
    settings = _make_settings()
    agent = MagicMock()
    agent.respond = AsyncMock(side_effect=TimeoutError())
    update = _make_update("hello")
    context = _make_context(agent, settings)

    before = _counter_value({"status": "timeout"})

    await on_message(update, context)

    update.message.reply_text.assert_awaited_once()
    reply = update.message.reply_text.await_args.args[0]
    assert "taking too long" in reply
    assert _counter_value({"status": "timeout"}) == before + 1


@pytest.mark.asyncio
async def test_on_message_handles_generic_error() -> None:
    settings = _make_settings()
    agent = MagicMock()
    agent.respond = AsyncMock(side_effect=RuntimeError("boom"))
    update = _make_update("hello")
    context = _make_context(agent, settings)

    before = _counter_value({"status": "error"})

    await on_message(update, context)

    update.message.reply_text.assert_awaited_once()
    reply = update.message.reply_text.await_args.args[0]
    assert "couldn't reach Claude" in reply
    assert _counter_value({"status": "error"}) == before + 1


@pytest.mark.asyncio
async def test_on_message_success_path() -> None:
    settings = _make_settings()
    agent = MagicMock()
    agent.respond = AsyncMock(return_value="hi back")
    update = _make_update("hello")
    context = _make_context(agent, settings)

    before = _counter_value({"status": "success"})

    await on_message(update, context)

    agent.respond.assert_awaited_once_with(session_id="telegram_1", prompt="hello")
    update.message.reply_text.assert_awaited_once_with("hi back")
    assert _counter_value({"status": "success"}) == before + 1


@pytest.mark.asyncio
async def test_on_message_returns_when_message_is_none() -> None:
    settings = _make_settings()
    agent = MagicMock()
    agent.respond = AsyncMock()
    update = MagicMock()
    update.message = None
    update.effective_user = MagicMock()
    context = _make_context(agent, settings)

    await on_message(update, context)

    agent.respond.assert_not_called()


@pytest.mark.asyncio
async def test_on_shutdown_clears_credentials_even_when_stop_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent = MagicMock()
    agent.stop = AsyncMock(side_effect=RuntimeError("stop failed"))
    identity = MagicMock()
    identity.stop = AsyncMock()

    app = MagicMock()
    app.bot_data = {AGENT_KEY: agent, IDENTITY_KEY: identity}

    with (
        patch("cleanrr.bot.clear_sdk_credentials") as mock_clear,
        caplog.at_level(logging.INFO, logger="cleanrr.bot"),
        pytest.raises(RuntimeError, match="stop failed"),
    ):
        await _on_shutdown(app)

    mock_clear.assert_called_once()
    assert "shutting down" in caplog.text


def test_configure_logging_silences_httpx() -> None:
    logging.getLogger("httpx").setLevel(logging.INFO)
    configure_logging("INFO")
    assert logging.getLogger("httpx").level >= logging.WARNING
