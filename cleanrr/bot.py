import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from cleanrr.agent import Agent
from cleanrr.config import Settings, export_sdk_credentials

logger = logging.getLogger(__name__)

AGENT_KEY = "agent"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "cleanrr is online. Ask me anything — fix actions land in a later phase."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Commands:\n"
        "/start — sanity check\n"
        "/help — this message\n\n"
        "Send any message and I'll reply via Claude."
    )


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    agent: Agent = context.application.bot_data[AGENT_KEY]
    user = update.effective_user
    text = update.message.text

    logger.info("message from %s (id=%s): %s", user.username, user.id, text[:80])
    reply = await agent.respond(session_id=f"telegram_{user.id}", prompt=text)
    await update.message.reply_text(reply or "(no reply)")


async def _on_startup(app: Application) -> None:
    await app.bot_data[AGENT_KEY].start()
    logger.info("cleanrr ready")


async def _on_shutdown(app: Application) -> None:
    await app.bot_data[AGENT_KEY].stop()


def build_application(settings: Settings) -> Application:
    app: Application = (
        Application.builder()
        .token(settings.telegram_bot_token.get_secret_value())
        .post_init(_on_startup)
        .post_shutdown(_on_shutdown)
        .build()
    )
    app.bot_data[AGENT_KEY] = Agent(
        model=settings.claude_model,
        system_prompt=settings.claude_system_prompt,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    return app


def main() -> None:
    settings = Settings()

    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    export_sdk_credentials(settings)

    app = build_application(settings)
    app.run_polling(allowed_updates=Update.ALL_TYPES)
