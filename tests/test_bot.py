from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cleanrr.agent_pool import AgentPool
from cleanrr.bot import (
    _QBIT_CLIENT_KEY,
    _RADARR_CLIENT_KEY,
    _SONARR_CLIENT_KEY,
    _on_shutdown,
    _on_startup,
    build_application,
    configure_logging,
)
from cleanrr.config import Settings
from cleanrr.handlers import (
    AGENT_POOL_KEY,
    CONFIRMATION_REGISTRY_KEY,
    IDENTITY_KEY,
    OVERSEERR_CLIENT_KEY,
    SETTINGS_KEY,
    cmd_help,
    cmd_invite,
    cmd_link,
    cmd_start,
    on_confirmation,
    on_error,
    on_message,
)
from cleanrr.identity import Identity


def _make_settings(
    metrics_enabled: bool = False,
    metrics_port: int = 9100,
) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="fake-bot-token",  # type: ignore[arg-type]
        anthropic_api_key="sk-fake",  # type: ignore[arg-type]
        metrics_enabled=metrics_enabled,
        metrics_port=metrics_port,
    )


@pytest.mark.asyncio
async def test_on_shutdown_clears_credentials_even_when_stop_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pool = MagicMock()
    pool.stop = AsyncMock(side_effect=RuntimeError("stop failed"))
    identity = MagicMock()
    identity.stop = AsyncMock()
    registry = MagicMock()
    registry.stop = AsyncMock()
    overseerr_client = MagicMock()
    overseerr_client.aclose = AsyncMock()

    app = MagicMock()
    app.bot_data = {
        AGENT_POOL_KEY: pool,
        IDENTITY_KEY: identity,
        CONFIRMATION_REGISTRY_KEY: registry,
        OVERSEERR_CLIENT_KEY: overseerr_client,
        _SONARR_CLIENT_KEY: None,
        _RADARR_CLIENT_KEY: None,
        _QBIT_CLIENT_KEY: None,
    }

    with (
        patch("cleanrr.bot.clear_sdk_credentials") as mock_clear,
        caplog.at_level(logging.INFO, logger="cleanrr.bot"),
        pytest.raises(RuntimeError, match="stop failed"),
    ):
        await _on_shutdown(app)

    identity.stop.assert_awaited_once()
    registry.stop.assert_awaited_once()
    overseerr_client.aclose.assert_awaited_once()
    mock_clear.assert_called_once()
    assert "shutting down" in caplog.text


def test_configure_logging_silences_httpx() -> None:
    logging.getLogger("httpx").setLevel(logging.INFO)
    configure_logging("INFO")
    assert logging.getLogger("httpx").level >= logging.WARNING


@pytest.mark.asyncio
async def test_on_startup_starts_identity() -> None:
    identity = MagicMock()
    identity.start = AsyncMock()
    identity.user_count = AsyncMock(return_value=0)
    registry = MagicMock()
    registry.start = AsyncMock()
    settings = _make_settings(metrics_enabled=False)

    app = MagicMock()
    app.bot_data = {
        IDENTITY_KEY: identity,
        SETTINGS_KEY: settings,
        CONFIRMATION_REGISTRY_KEY: registry,
    }
    app.bot.set_my_commands = AsyncMock()

    with patch("cleanrr.bot.metrics.start") as mock_metrics_start:
        await _on_startup(app)

    registry.start.assert_awaited_once()
    identity.start.assert_awaited_once()
    app.bot.set_my_commands.assert_awaited_once()
    mock_metrics_start.assert_not_called()


@pytest.mark.asyncio
async def test_on_startup_starts_metrics_when_enabled() -> None:
    identity = MagicMock()
    identity.start = AsyncMock()
    identity.user_count = AsyncMock(return_value=7)
    registry = MagicMock()
    registry.start = AsyncMock()
    settings = _make_settings(metrics_enabled=True, metrics_port=9200)

    app = MagicMock()
    app.bot_data = {
        IDENTITY_KEY: identity,
        SETTINGS_KEY: settings,
        CONFIRMATION_REGISTRY_KEY: registry,
    }
    app.bot.set_my_commands = AsyncMock()

    with (
        patch("cleanrr.bot.metrics.start") as mock_metrics_start,
        patch("cleanrr.bot.metrics.linked_users") as mock_linked_users,
    ):
        await _on_startup(app)

    mock_metrics_start.assert_called_once_with(9200, str(settings.metrics_bind_address))
    mock_linked_users.set.assert_called_once_with(7)


def test_build_application_wires_bot_data_and_handlers() -> None:
    settings = _make_settings()
    app = build_application(settings)

    assert app.bot_data[SETTINGS_KEY] is settings
    assert isinstance(app.bot_data[AGENT_POOL_KEY], AgentPool)
    assert isinstance(app.bot_data[IDENTITY_KEY], Identity)

    registered = [handler.callback for handler in app.handlers[0]]
    assert registered == [cmd_start, cmd_help, cmd_invite, cmd_link, on_confirmation, on_message]
    # Must be enabled — a confirmation button tap has to reach on_confirmation
    # while the message that triggered it is still blocked awaiting that tap.
    assert app.concurrent_updates
    # Without this, an exception a handler doesn't catch itself (e.g.
    # reply_text raising Forbidden) is only logged internally by PTB and the
    # update is dropped with no other trace.
    assert on_error in app.error_handlers
