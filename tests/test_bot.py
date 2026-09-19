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
from cleanrr.link_migration import BackfillResult


def _make_settings(
    metrics_enabled: bool = False,
    metrics_port: int = 9100,
    admin_telegram_ids: set[int] | None = None,
) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="fake-bot-token",  # type: ignore[arg-type]
        anthropic_api_key="sk-fake",  # type: ignore[arg-type]
        metrics_enabled=metrics_enabled,
        metrics_port=metrics_port,
        admin_telegram_ids=admin_telegram_ids or set(),
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
    identity.count_links_needing_overseerr_user_id = AsyncMock(return_value=0)
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

    with (
        patch("cleanrr.bot.metrics.start") as mock_metrics_start,
        patch(
            "cleanrr.bot.backfill_overseerr_user_ids",
            AsyncMock(return_value=BackfillResult(0, 0)),
        ),
    ):
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
    identity.count_links_needing_overseerr_user_id = AsyncMock(return_value=0)
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
        patch(
            "cleanrr.bot.backfill_overseerr_user_ids",
            AsyncMock(return_value=BackfillResult(0, 0)),
        ),
    ):
        await _on_startup(app)

    mock_metrics_start.assert_called_once_with(9200, str(settings.metrics_bind_address))
    mock_linked_users.set.assert_called_once_with(7)


@pytest.mark.asyncio
async def test_on_startup_warns_when_no_admins_configured(
    caplog: pytest.LogCaptureFixture,
) -> None:
    identity = MagicMock()
    identity.start = AsyncMock()
    identity.user_count = AsyncMock(return_value=0)
    identity.count_links_needing_overseerr_user_id = AsyncMock(return_value=0)
    registry = MagicMock()
    registry.start = AsyncMock()
    settings = _make_settings()

    app = MagicMock()
    app.bot_data = {
        IDENTITY_KEY: identity,
        SETTINGS_KEY: settings,
        CONFIRMATION_REGISTRY_KEY: registry,
    }
    app.bot.set_my_commands = AsyncMock()

    with (
        caplog.at_level(logging.WARNING, logger="cleanrr.bot"),
        patch("cleanrr.bot.metrics.start"),
        patch(
            "cleanrr.bot.backfill_overseerr_user_ids",
            AsyncMock(return_value=BackfillResult(0, 0)),
        ),
    ):
        await _on_startup(app)

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "ADMIN_TELEGRAM_IDS" in warnings[0].message
    assert "/invite" in warnings[0].message


@pytest.mark.asyncio
async def test_on_startup_does_not_warn_when_admins_configured(
    caplog: pytest.LogCaptureFixture,
) -> None:
    identity = MagicMock()
    identity.start = AsyncMock()
    identity.user_count = AsyncMock(return_value=2)
    identity.count_links_needing_overseerr_user_id = AsyncMock(return_value=0)
    registry = MagicMock()
    registry.start = AsyncMock()
    settings = _make_settings(admin_telegram_ids={424242, 515151})

    app = MagicMock()
    app.bot_data = {
        IDENTITY_KEY: identity,
        SETTINGS_KEY: settings,
        CONFIRMATION_REGISTRY_KEY: registry,
    }
    app.bot.set_my_commands = AsyncMock()

    with (
        caplog.at_level(logging.WARNING, logger="cleanrr.bot"),
        patch("cleanrr.bot.metrics.start"),
        patch(
            "cleanrr.bot.backfill_overseerr_user_ids",
            AsyncMock(return_value=BackfillResult(0, 0)),
        ),
    ):
        await _on_startup(app)

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_on_startup_logs_admin_count_not_ids(
    caplog: pytest.LogCaptureFixture,
) -> None:
    identity = MagicMock()
    identity.start = AsyncMock()
    identity.user_count = AsyncMock(return_value=2)
    identity.count_links_needing_overseerr_user_id = AsyncMock(return_value=0)
    registry = MagicMock()
    registry.start = AsyncMock()
    settings = _make_settings(admin_telegram_ids={424242, 515151})

    app = MagicMock()
    app.bot_data = {
        IDENTITY_KEY: identity,
        SETTINGS_KEY: settings,
        CONFIRMATION_REGISTRY_KEY: registry,
    }
    app.bot.set_my_commands = AsyncMock()

    with (
        caplog.at_level(logging.INFO, logger="cleanrr.bot"),
        patch("cleanrr.bot.metrics.start"),
        patch(
            "cleanrr.bot.backfill_overseerr_user_ids",
            AsyncMock(return_value=BackfillResult(0, 0)),
        ),
    ):
        await _on_startup(app)

    assert "2 admin Telegram ID(s) configured" in caplog.text
    assert "424242" not in caplog.text
    assert "515151" not in caplog.text


@pytest.mark.asyncio
async def test_on_startup_awaits_backfill_with_identity_client_and_settings() -> None:
    identity = MagicMock()
    identity.start = AsyncMock()
    identity.user_count = AsyncMock(return_value=0)
    identity.count_links_needing_overseerr_user_id = AsyncMock(return_value=0)
    registry = MagicMock()
    registry.start = AsyncMock()
    settings = _make_settings()
    overseerr_client = MagicMock()

    app = MagicMock()
    app.bot_data = {
        IDENTITY_KEY: identity,
        SETTINGS_KEY: settings,
        CONFIRMATION_REGISTRY_KEY: registry,
        OVERSEERR_CLIENT_KEY: overseerr_client,
    }
    app.bot.set_my_commands = AsyncMock()

    with (
        patch("cleanrr.bot.metrics.start"),
        patch(
            "cleanrr.bot.backfill_overseerr_user_ids",
            AsyncMock(return_value=BackfillResult(0, 0)),
        ) as mock_backfill,
    ):
        await _on_startup(app)

    mock_backfill.assert_awaited_once_with(identity, overseerr_client, settings)


@pytest.mark.asyncio
async def test_on_startup_backfills_with_none_client_when_key_missing() -> None:
    identity = MagicMock()
    identity.start = AsyncMock()
    identity.user_count = AsyncMock(return_value=0)
    identity.count_links_needing_overseerr_user_id = AsyncMock(return_value=0)
    registry = MagicMock()
    registry.start = AsyncMock()
    settings = _make_settings()

    app = MagicMock()
    app.bot_data = {
        IDENTITY_KEY: identity,
        SETTINGS_KEY: settings,
        CONFIRMATION_REGISTRY_KEY: registry,
    }
    app.bot.set_my_commands = AsyncMock()

    with (
        patch("cleanrr.bot.metrics.start"),
        patch(
            "cleanrr.bot.backfill_overseerr_user_ids",
            AsyncMock(return_value=BackfillResult(0, 0)),
        ) as mock_backfill,
    ):
        await _on_startup(app)

    mock_backfill.assert_awaited_once_with(identity, None, settings)
    app.bot.set_my_commands.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_startup_backfill_timeout_does_not_stop_startup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    identity = MagicMock()
    identity.start = AsyncMock()
    identity.user_count = AsyncMock(return_value=0)
    identity.count_links_needing_overseerr_user_id = AsyncMock(return_value=0)
    registry = MagicMock()
    registry.start = AsyncMock()
    settings = _make_settings()

    app = MagicMock()
    app.bot_data = {
        IDENTITY_KEY: identity,
        SETTINGS_KEY: settings,
        CONFIRMATION_REGISTRY_KEY: registry,
    }
    app.bot.set_my_commands = AsyncMock()

    with (
        caplog.at_level(logging.WARNING, logger="cleanrr.bot"),
        patch("cleanrr.bot.metrics.start"),
        patch("cleanrr.bot.backfill_overseerr_user_ids", AsyncMock(side_effect=TimeoutError)),
    ):
        await _on_startup(app)

    assert "link migration timed out" in caplog.text
    app.bot.set_my_commands.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_startup_backfill_error_does_not_stop_startup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    identity = MagicMock()
    identity.start = AsyncMock()
    identity.user_count = AsyncMock(return_value=0)
    identity.count_links_needing_overseerr_user_id = AsyncMock(return_value=0)
    registry = MagicMock()
    registry.start = AsyncMock()
    settings = _make_settings()

    app = MagicMock()
    app.bot_data = {
        IDENTITY_KEY: identity,
        SETTINGS_KEY: settings,
        CONFIRMATION_REGISTRY_KEY: registry,
    }
    app.bot.set_my_commands = AsyncMock()

    with (
        caplog.at_level(logging.WARNING, logger="cleanrr.bot"),
        patch("cleanrr.bot.metrics.start"),
        patch(
            "cleanrr.bot.backfill_overseerr_user_ids",
            AsyncMock(side_effect=RuntimeError("boom")),
        ),
    ):
        await _on_startup(app)

    assert "link migration failed" in caplog.text
    app.bot.set_my_commands.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_startup_sets_missing_gauge_when_metrics_enabled() -> None:
    identity = MagicMock()
    identity.start = AsyncMock()
    identity.user_count = AsyncMock(return_value=0)
    identity.count_links_needing_overseerr_user_id = AsyncMock(return_value=2)
    registry = MagicMock()
    registry.start = AsyncMock()
    settings = _make_settings(metrics_enabled=True)

    app = MagicMock()
    app.bot_data = {
        IDENTITY_KEY: identity,
        SETTINGS_KEY: settings,
        CONFIRMATION_REGISTRY_KEY: registry,
    }
    app.bot.set_my_commands = AsyncMock()

    with (
        patch("cleanrr.bot.metrics.start"),
        patch("cleanrr.bot.metrics.links_missing_overseerr_user_id") as mock_gauge,
        patch(
            "cleanrr.bot.backfill_overseerr_user_ids",
            AsyncMock(return_value=BackfillResult(0, 2)),
        ),
    ):
        await _on_startup(app)

    mock_gauge.set.assert_called_once_with(2)


@pytest.mark.asyncio
async def test_on_startup_does_not_set_missing_gauge_when_metrics_disabled() -> None:
    identity = MagicMock()
    identity.start = AsyncMock()
    identity.user_count = AsyncMock(return_value=0)
    identity.count_links_needing_overseerr_user_id = AsyncMock(return_value=2)
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

    with (
        patch("cleanrr.bot.metrics.start") as mock_metrics_start,
        patch("cleanrr.bot.metrics.links_missing_overseerr_user_id") as mock_gauge,
        patch(
            "cleanrr.bot.backfill_overseerr_user_ids",
            AsyncMock(return_value=BackfillResult(0, 2)),
        ),
    ):
        await _on_startup(app)

    mock_gauge.set.assert_not_called()
    mock_metrics_start.assert_not_called()


@pytest.mark.asyncio
async def test_on_startup_runs_backfill_before_reading_the_gauge() -> None:
    order: list[str] = []

    async def _fake_backfill(*_args: object, **_kwargs: object) -> BackfillResult:
        order.append("backfill")
        return BackfillResult(0, 0)

    async def _fake_count() -> int:
        order.append("gauge_read")
        return 0

    identity = MagicMock()
    identity.start = AsyncMock()
    identity.user_count = AsyncMock(return_value=0)
    identity.count_links_needing_overseerr_user_id = _fake_count
    registry = MagicMock()
    registry.start = AsyncMock()
    settings = _make_settings(metrics_enabled=True)

    app = MagicMock()
    app.bot_data = {
        IDENTITY_KEY: identity,
        SETTINGS_KEY: settings,
        CONFIRMATION_REGISTRY_KEY: registry,
    }
    app.bot.set_my_commands = AsyncMock()

    with (
        patch("cleanrr.bot.metrics.start"),
        patch("cleanrr.bot.metrics.links_missing_overseerr_user_id"),
        patch("cleanrr.bot.backfill_overseerr_user_ids", _fake_backfill),
    ):
        await _on_startup(app)

    assert order == ["backfill", "gauge_read"]


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
