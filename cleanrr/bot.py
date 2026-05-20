import logging
import time
from datetime import timedelta

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import cleanrr.metrics as metrics
from cleanrr.agent import Agent
from cleanrr.config import Settings, clear_sdk_credentials, export_sdk_credentials
from cleanrr.identity import Identity

logger = logging.getLogger(__name__)

AGENT_KEY = "agent"
IDENTITY_KEY = "identity"
SETTINGS_KEY = "settings"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    metrics.telegram_messages_total.labels(kind="command", command="start").inc()
    await update.message.reply_text(
        "cleanrr is online. Ask me anything — fix actions land in a later phase."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    metrics.telegram_messages_total.labels(kind="command", command="help").inc()
    await update.message.reply_text(
        "Commands:\n"
        "/start — sanity check\n"
        "/help — this message\n"
        "/link <code> — bind your Telegram account to an Overseerr user\n"
        "/invite <overseerr_username> — admin only; issue a link code\n\n"
        "Send any message and I'll reply via Claude."
    )


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # PTB types every Update field as Optional, but our filters guarantee these are present.
    if update.message is None or update.message.text is None or update.effective_user is None:
        return

    agent: Agent = context.application.bot_data[AGENT_KEY]
    settings: Settings = context.application.bot_data[SETTINGS_KEY]
    user = update.effective_user
    text = update.message.text

    metrics.telegram_messages_total.labels(kind="text", command="").inc()

    if len(text) > settings.telegram_max_message_chars:
        limit = settings.telegram_max_message_chars
        metrics.claude_requests_total.labels(status="rejected_too_long").inc()
        await update.message.reply_text(
            f"That message is over the {limit}-char limit — try splitting it up."
        )
        return

    logger.info("message from %s (id=%s): %s", user.username, user.id, text[:80])

    start = time.perf_counter()
    try:
        reply = await agent.respond(session_id=f"telegram_{user.id}", prompt=text)
    except TimeoutError:
        logger.warning("agent.respond timed out after %.0fs", settings.claude_timeout_seconds)
        metrics.claude_request_duration_seconds.observe(time.perf_counter() - start)
        metrics.claude_requests_total.labels(status="timeout").inc()
        await update.message.reply_text("Claude is taking too long — try again in a moment.")
        return
    except Exception:
        logger.exception("agent.respond failed")
        metrics.claude_request_duration_seconds.observe(time.perf_counter() - start)
        metrics.claude_requests_total.labels(status="error").inc()
        await update.message.reply_text(
            "Sorry — I couldn't reach Claude just now. Try again in a moment."
        )
        return
    metrics.claude_request_duration_seconds.observe(time.perf_counter() - start)
    metrics.claude_requests_total.labels(status="success").inc()
    await update.message.reply_text(reply or "(no reply)")


async def cmd_invite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    metrics.telegram_messages_total.labels(kind="command", command="invite").inc()
    settings: Settings = context.application.bot_data[SETTINGS_KEY]
    if not settings.admin_telegram_ids:
        await update.message.reply_text(
            "/invite is disabled — set ADMIN_TELEGRAM_IDS in .env to enable it."
        )
        return
    if update.effective_user.id not in settings.admin_telegram_ids:
        await update.message.reply_text("/invite is admin-only.")
        return

    args = context.args or []
    if len(args) != 1:
        await update.message.reply_text("Usage: /invite <overseerr_username>")
        return

    overseerr_username = args[0].lstrip("@")
    identity: Identity = context.application.bot_data[IDENTITY_KEY]
    code = await identity.issue_code(overseerr_username)
    await update.message.reply_text(
        f"Link code for @{overseerr_username}: {code}\n"
        f"Expires in {settings.link_code_ttl_hours}h. Share it; they DM me /link {code}."
    )


async def cmd_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    metrics.telegram_messages_total.labels(kind="command", command="link").inc()
    args = context.args or []
    if len(args) != 1:
        await update.message.reply_text("Usage: /link <code>")
        return

    code = args[0].upper()
    identity: Identity = context.application.bot_data[IDENTITY_KEY]
    overseerr_username = await identity.redeem_code(code, update.effective_user.id)
    if overseerr_username is None:
        await update.message.reply_text(
            "That code didn't work — wrong, expired, or already used. Ask the admin for a new one."
        )
        return

    await update.message.reply_text(
        f"Linked you to Overseerr user @{overseerr_username}. You're set."
    )


async def _on_startup(app: Application) -> None:
    await app.bot_data[AGENT_KEY].start()
    identity: Identity = app.bot_data[IDENTITY_KEY]
    await identity.start()
    settings: Settings = app.bot_data[SETTINGS_KEY]
    if settings.metrics_enabled:
        metrics.start(settings.metrics_port, str(settings.metrics_bind_address))
        metrics.linked_users.set(await identity.user_count())
        logger.info("metrics on %s:%d", settings.metrics_bind_address, settings.metrics_port)
    logger.info("cleanrr ready")


async def _on_shutdown(app: Application) -> None:
    agent_error = None
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
    app.bot_data[AGENT_KEY] = Agent(
        model=settings.claude_model,
        system_prompt=settings.claude_system_prompt,
        timeout_seconds=settings.claude_timeout_seconds,
    )
    app.bot_data[IDENTITY_KEY] = Identity(
        db_path=settings.database_path,
        code_ttl=timedelta(hours=settings.link_code_ttl_hours),
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("invite", cmd_invite))
    app.add_handler(CommandHandler("link", cmd_link))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    return app


def main() -> None:
    settings = Settings()  # type: ignore[call-arg]  # populated from .env at runtime

    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    export_sdk_credentials(settings)

    app = build_application(settings)
    app.run_polling(allowed_updates=Update.ALL_TYPES)
