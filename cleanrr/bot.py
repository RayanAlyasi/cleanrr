import logging
import re
from datetime import timedelta

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
    on_message,
)
from cleanrr.identity import Identity
from cleanrr.permissions import CALLBACK_PREFIX

logger = logging.getLogger(__name__)

# Registered with Telegram via set_my_commands so the "/" autocomplete menu
# lists them; must stay in sync with the CommandHandler registrations below.
BOT_COMMANDS = [
    BotCommand("start", "Sanity check; bot confirms it's online"),
    BotCommand("help", "List the commands available"),
    BotCommand("link", "Bind your Telegram account to an Overseerr user"),
    BotCommand("invite", "Admin only — issue a link code for a friend"),
]


async def _on_startup(app: Application) -> None:
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
        .build()
    )
    app.bot_data[SETTINGS_KEY] = settings
    identity = Identity(
        db_path=settings.database_path,
        code_ttl=timedelta(hours=settings.link_code_ttl_hours),
    )
    app.bot_data[IDENTITY_KEY] = identity
    app.bot_data[AGENT_KEY] = Agent(
        identity=identity,
        settings=settings,
        model=settings.claude_model,
        system_prompt=settings.claude_system_prompt,
        timeout_seconds=settings.claude_timeout_seconds,
        telegram_bot=app.bot,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("invite", cmd_invite))
    app.add_handler(CommandHandler("link", cmd_link))
    app.add_handler(CallbackQueryHandler(on_confirmation, pattern=f"^{re.escape(CALLBACK_PREFIX)}"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
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
