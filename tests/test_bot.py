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
    _on_startup,
    cmd_help,
    cmd_invite,
    cmd_link,
    cmd_start,
    configure_logging,
    on_message,
)
from cleanrr.config import Settings


def _make_settings(
    max_chars: int = 2000,
    timeout: float = 30.0,
    admin_ids: set[int] | None = None,
    metrics_enabled: bool = False,
    metrics_port: int = 9100,
) -> Settings:
    kwargs: dict[str, object] = {
        "_env_file": None,
        "telegram_bot_token": "fake-bot-token",
        "anthropic_api_key": "sk-fake",
        "telegram_max_message_chars": max_chars,
        "claude_timeout_seconds": timeout,
        "metrics_enabled": metrics_enabled,
        "metrics_port": metrics_port,
    }
    if admin_ids is not None:
        kwargs["admin_telegram_ids"] = admin_ids
    return Settings(**kwargs)  # type: ignore[arg-type]


def _make_update(text: str, user_id: int = 1) -> MagicMock:
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = "alice"
    return update


def _make_context(
    agent: MagicMock,
    settings: Settings,
    identity: MagicMock | None = None,
) -> MagicMock:
    context = MagicMock()
    bot_data: dict[str, object] = {AGENT_KEY: agent, SETTINGS_KEY: settings}
    if identity is not None:
        bot_data[IDENTITY_KEY] = identity
    context.application.bot_data = bot_data
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

    agent.respond.assert_awaited_once_with(telegram_user_id=1, prompt="hello")
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


# ---------------------------------------------------------------------------
# cmd_invite
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_invite_no_message() -> None:
    update = MagicMock()
    update.message = None
    update.effective_user = MagicMock()
    context = _make_context(MagicMock(), _make_settings())

    await cmd_invite(update, context)

    # No reply possible; just verify we return cleanly without AttributeError.


@pytest.mark.asyncio
async def test_cmd_invite_no_effective_user() -> None:
    update = MagicMock()
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_user = None
    context = _make_context(MagicMock(), _make_settings())

    await cmd_invite(update, context)

    update.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_cmd_invite_disabled_when_no_admin_ids() -> None:
    settings = _make_settings(admin_ids=set())
    update = _make_update("", user_id=1)
    context = _make_context(MagicMock(), settings)

    await cmd_invite(update, context)

    reply = update.message.reply_text.await_args.args[0]
    assert "is disabled" in reply
    assert "ADMIN_TELEGRAM_IDS" in reply


@pytest.mark.asyncio
async def test_cmd_invite_rejects_non_admin_caller() -> None:
    settings = _make_settings(admin_ids={99})
    identity = MagicMock()
    identity.issue_code = AsyncMock(return_value="XYZ")
    update = _make_update("", user_id=1)
    context = _make_context(MagicMock(), settings, identity=identity)

    await cmd_invite(update, context)

    reply = update.message.reply_text.await_args.args[0]
    assert "admin-only" in reply
    identity.issue_code.assert_not_awaited()


@pytest.mark.asyncio
async def test_cmd_invite_rejects_missing_args() -> None:
    settings = _make_settings(admin_ids={1})
    identity = MagicMock()
    identity.issue_code = AsyncMock(return_value="ABC123")
    update = _make_update("", user_id=1)
    context = _make_context(MagicMock(), settings, identity=identity)
    context.args = []

    await cmd_invite(update, context)

    reply = update.message.reply_text.await_args.args[0]
    assert "Usage: /invite" in reply
    identity.issue_code.assert_not_awaited()


@pytest.mark.asyncio
async def test_cmd_invite_rejects_too_many_args() -> None:
    settings = _make_settings(admin_ids={1})
    identity = MagicMock()
    identity.issue_code = AsyncMock(return_value="ABC123")
    update = _make_update("", user_id=1)
    context = _make_context(MagicMock(), settings, identity=identity)
    context.args = ["a", "b"]

    await cmd_invite(update, context)

    reply = update.message.reply_text.await_args.args[0]
    assert "Usage: /invite" in reply
    identity.issue_code.assert_not_awaited()


@pytest.mark.asyncio
async def test_cmd_invite_strips_leading_at_sign() -> None:
    settings = _make_settings(admin_ids={1})
    identity = MagicMock()
    identity.issue_code = AsyncMock(return_value="ABC123")
    update = _make_update("", user_id=1)
    context = _make_context(MagicMock(), settings, identity=identity)
    context.args = ["@bob"]

    await cmd_invite(update, context)

    identity.issue_code.assert_awaited_once_with("bob")


@pytest.mark.asyncio
async def test_cmd_invite_happy_path() -> None:
    settings = _make_settings(admin_ids={1})
    identity = MagicMock()
    identity.issue_code = AsyncMock(return_value="ABC123")
    update = _make_update("", user_id=1)
    context = _make_context(MagicMock(), settings, identity=identity)
    context.args = ["alice"]

    await cmd_invite(update, context)

    identity.issue_code.assert_awaited_once_with("alice")
    reply = update.message.reply_text.await_args.args[0]
    assert "ABC123" in reply
    assert str(settings.link_code_ttl_hours) in reply


# ---------------------------------------------------------------------------
# cmd_link
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_link_no_message() -> None:
    update = MagicMock()
    update.message = None
    update.effective_user = MagicMock()
    context = _make_context(MagicMock(), _make_settings())

    await cmd_link(update, context)


@pytest.mark.asyncio
async def test_cmd_link_rejects_missing_args() -> None:
    identity = MagicMock()
    identity.redeem_code = AsyncMock(return_value=None)
    update = _make_update("", user_id=1)
    context = _make_context(MagicMock(), _make_settings(), identity=identity)
    context.args = []

    await cmd_link(update, context)

    reply = update.message.reply_text.await_args.args[0]
    assert "Usage: /link" in reply
    identity.redeem_code.assert_not_awaited()


@pytest.mark.asyncio
async def test_cmd_link_uppercases_code() -> None:
    identity = MagicMock()
    identity.redeem_code = AsyncMock(return_value="alice")
    update = _make_update("", user_id=1)
    context = _make_context(MagicMock(), _make_settings(), identity=identity)
    context.args = ["abc"]

    await cmd_link(update, context)

    identity.redeem_code.assert_awaited_once_with("ABC", 1)


@pytest.mark.asyncio
async def test_cmd_link_invalid_code() -> None:
    identity = MagicMock()
    identity.redeem_code = AsyncMock(return_value=None)
    update = _make_update("", user_id=1)
    context = _make_context(MagicMock(), _make_settings(), identity=identity)
    context.args = ["BADCODE"]

    await cmd_link(update, context)

    reply = update.message.reply_text.await_args.args[0]
    assert "didn't work" in reply


@pytest.mark.asyncio
async def test_cmd_link_happy_path() -> None:
    identity = MagicMock()
    identity.redeem_code = AsyncMock(return_value="alice")
    update = _make_update("", user_id=1)
    context = _make_context(MagicMock(), _make_settings(), identity=identity)
    context.args = ["VALIDCODE"]

    await cmd_link(update, context)

    reply = update.message.reply_text.await_args.args[0]
    assert "Linked you to Overseerr user @alice" in reply


# ---------------------------------------------------------------------------
# cmd_start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_start_no_message() -> None:
    update = MagicMock()
    update.message = None
    context = _make_context(MagicMock(), _make_settings())

    await cmd_start(update, context)


@pytest.mark.asyncio
async def test_cmd_start_happy_path() -> None:
    update = _make_update("", user_id=1)
    context = _make_context(MagicMock(), _make_settings())

    before = metrics.telegram_messages_total.labels(kind="command", command="start")._value.get()

    await cmd_start(update, context)

    reply = update.message.reply_text.await_args.args[0]
    assert "cleanrr is online" in reply
    assert (
        metrics.telegram_messages_total.labels(kind="command", command="start")._value.get()
        == before + 1
    )


# ---------------------------------------------------------------------------
# cmd_help
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_help_no_message() -> None:
    update = MagicMock()
    update.message = None
    context = _make_context(MagicMock(), _make_settings())

    await cmd_help(update, context)


@pytest.mark.asyncio
async def test_cmd_help_lists_commands() -> None:
    update = _make_update("", user_id=1)
    context = _make_context(MagicMock(), _make_settings())

    await cmd_help(update, context)

    reply = update.message.reply_text.await_args.args[0]
    assert "/start" in reply
    assert "/help" in reply
    assert "/link" in reply
    assert "/invite" in reply


# ---------------------------------------------------------------------------
# _on_startup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_startup_starts_agent_and_identity() -> None:
    agent = MagicMock()
    agent.start = AsyncMock()
    identity = MagicMock()
    identity.start = AsyncMock()
    identity.user_count = AsyncMock(return_value=0)
    settings = _make_settings(metrics_enabled=False)

    app = MagicMock()
    app.bot_data = {AGENT_KEY: agent, IDENTITY_KEY: identity, SETTINGS_KEY: settings}

    with patch("cleanrr.bot.metrics.start") as mock_metrics_start:
        await _on_startup(app)

    agent.start.assert_awaited_once()
    identity.start.assert_awaited_once()
    mock_metrics_start.assert_not_called()


@pytest.mark.asyncio
async def test_on_startup_starts_metrics_when_enabled() -> None:
    agent = MagicMock()
    agent.start = AsyncMock()
    identity = MagicMock()
    identity.start = AsyncMock()
    identity.user_count = AsyncMock(return_value=7)
    settings = _make_settings(metrics_enabled=True, metrics_port=9200)

    app = MagicMock()
    app.bot_data = {AGENT_KEY: agent, IDENTITY_KEY: identity, SETTINGS_KEY: settings}

    with (
        patch("cleanrr.bot.metrics.start") as mock_metrics_start,
        patch("cleanrr.bot.metrics.linked_users") as mock_linked_users,
    ):
        await _on_startup(app)

    mock_metrics_start.assert_called_once_with(9200, str(settings.metrics_bind_address))
    mock_linked_users.set.assert_called_once_with(7)
