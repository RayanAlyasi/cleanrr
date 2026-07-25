import logging
import re
from datetime import timedelta

import httpx
from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

import cleanrr.metrics as metrics
from cleanrr.agent import Agent
from cleanrr.config import Settings, clear_sdk_credentials, export_sdk_credentials
from cleanrr.handlers import (
    AGENT_KEY,
    IDENTITY_KEY,
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
from cleanrr.permissions import CALLBACK_PREFIX, ConfirmationRegistry

logger = logging.getLogger(__name__)

# Registered with Telegram via set_my_commands so the "/" autocomplete menu
# lists them; must stay in sync with the CommandHandler registrations below.
BOT_COMMANDS = [
    BotCommand("start", "Sanity check; bot confirms it's online"),
    BotCommand("help", "List the commands available"),
    BotCommand("link", "Bind your Telegram account to an Overseerr user"),
    BotCommand("invite", "Admin only — issue a link code for a friend"),
]

# Shared, bot-level resources — constructed once in build_application() and
# injected into Agent (and, eventually, every per-user Agent) rather than
# each Agent building its own copy. Keys are module-private; nothing outside
# bot.py's own startup/shutdown needs to reach into them.
_OVERSEERR_CLIENT_KEY = "overseerr_client"
_SONARR_CLIENT_KEY = "sonarr_client"
_RADARR_CLIENT_KEY = "radarr_client"
_QBIT_CLIENT_KEY = "qbit_client"
_CONFIRMATION_REGISTRY_KEY = "confirmation_registry"


def _build_overseerr_client(settings: Settings) -> httpx.AsyncClient | None:
    if settings.overseerr_url is None or settings.overseerr_api_key is None:
        return None
    return httpx.AsyncClient(
        headers={"X-Api-Key": settings.overseerr_api_key.get_secret_value()},
        timeout=settings.overseerr_timeout_seconds,
    )


def _build_sonarr_client(settings: Settings) -> httpx.AsyncClient | None:
    if settings.sonarr_url is None or settings.sonarr_api_key is None:
        return None
    return httpx.AsyncClient(
        headers={"X-Api-Key": settings.sonarr_api_key.get_secret_value()},
        timeout=settings.sonarr_timeout_seconds,
    )


def _build_radarr_client(settings: Settings) -> httpx.AsyncClient | None:
    if settings.radarr_url is None or settings.radarr_api_key is None:
        return None
    return httpx.AsyncClient(
        headers={"X-Api-Key": settings.radarr_api_key.get_secret_value()},
        timeout=settings.radarr_timeout_seconds,
    )


def _build_qbit_client(settings: Settings) -> httpx.AsyncClient | None:
    if (
        settings.qbittorrent_url is None
        or settings.qbittorrent_username is None
        or settings.qbittorrent_password is None
    ):
        return None
    return httpx.AsyncClient(timeout=settings.qbittorrent_timeout_seconds)


async def _on_startup(app: Application) -> None:
    registry: ConfirmationRegistry = app.bot_data[_CONFIRMATION_REGISTRY_KEY]
    await registry.start()
    await app.bot_data[AGENT_KEY].start()
    identity: Identity = app.bot_data[IDENTITY_KEY]
    await identity.start()
    await app.bot.set_my_commands(BOT_COMMANDS)
    settings: Settings = app.bot_data[SETTINGS_KEY]
    if settings.metrics_enabled:
        metrics.start(settings.metrics_port, str(settings.metrics_bind_address))
        metrics.linked_users.set(await identity.user_count())
        logger.info("metrics on %s:%d", settings.metrics_bind_address, settings.metrics_port)
    logger.info("cleanrr ready")


async def _on_shutdown(app: Application) -> None:
    logger.info("shutting down")
    agent_error = None
    # Stop Agent first so any in-flight tool handlers can still resolve Identity.
    try:
        await app.bot_data[AGENT_KEY].stop()
    except Exception as e:
        agent_error = e
    try:
        await app.bot_data[IDENTITY_KEY].stop()
        await app.bot_data[_CONFIRMATION_REGISTRY_KEY].stop()
        for key in (
            _OVERSEERR_CLIENT_KEY,
            _SONARR_CLIENT_KEY,
            _RADARR_CLIENT_KEY,
            _QBIT_CLIENT_KEY,
        ):
            client: httpx.AsyncClient | None = app.bot_data.get(key)
            if client is not None:
                await client.aclose()
    finally:
        clear_sdk_credentials()
        if agent_error is not None:
            raise agent_error


def build_application(settings: Settings) -> Application:
    app: Application = (
        Application.builder()
        .token(settings.telegram_bot_token.get_secret_value())
        .post_init(_on_startup)
        .post_shutdown(_on_shutdown)
        # Without this, PTB processes updates one at a time. A confirmation
        # button tap is a separate update from the message that triggered it,
        # and can_use_tool blocks the triggering update's own handler while it
        # awaits that tap — so the tap could never be dispatched until the
        # confirmation timed out on its own, by which point Telegram had
        # already invalidated the callback query. cleanrr's own concurrency
        # limit (Agent._lock, one Claude subprocess at a time) is unaffected —
        # this only lets otherwise-independent updates interleave.
        .concurrent_updates(True)
        .build()
    )
    app.bot_data[SETTINGS_KEY] = settings
    identity = Identity(
        db_path=settings.database_path,
        code_ttl=timedelta(hours=settings.link_code_ttl_hours),
    )
    app.bot_data[IDENTITY_KEY] = identity

    overseerr_client = _build_overseerr_client(settings)
    sonarr_client = _build_sonarr_client(settings)
    radarr_client = _build_radarr_client(settings)
    qbit_client = _build_qbit_client(settings)
    confirmation_registry = ConfirmationRegistry(ttl_seconds=settings.confirmation_ttl_seconds)
    app.bot_data[_OVERSEERR_CLIENT_KEY] = overseerr_client
    app.bot_data[_SONARR_CLIENT_KEY] = sonarr_client
    app.bot_data[_RADARR_CLIENT_KEY] = radarr_client
    app.bot_data[_QBIT_CLIENT_KEY] = qbit_client
    app.bot_data[_CONFIRMATION_REGISTRY_KEY] = confirmation_registry

    app.bot_data[AGENT_KEY] = Agent(
        identity=identity,
        settings=settings,
        model=settings.claude_model,
        system_prompt=settings.claude_system_prompt,
        timeout_seconds=settings.claude_timeout_seconds,
        telegram_bot=app.bot,
        overseerr_client=overseerr_client,
        sonarr_client=sonarr_client,
        radarr_client=radarr_client,
        qbit_client=qbit_client,
        confirmation_registry=confirmation_registry,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("invite", cmd_invite))
    app.add_handler(CommandHandler("link", cmd_link))
    app.add_handler(CallbackQueryHandler(on_confirmation, pattern=f"^{re.escape(CALLBACK_PREFIX)}"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_error_handler(on_error)
    return app


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # httpx logs full request URLs at INFO. Telegram's API uses /bot<TOKEN>/method
    # paths, so those INFO logs contain the bot token. Suppress the noise — errors
    # still propagate at WARNING and above.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:  # pragma: no cover
    settings = Settings()  # type: ignore[call-arg]  # populated from .env at runtime
    configure_logging(settings.log_level)
    export_sdk_credentials(settings)

    app = build_application(settings)
    app.run_polling(allowed_updates=Update.ALL_TYPES)
