import logging
import time
from typing import Any

from telegram import CallbackQuery, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import cleanrr.metrics as metrics
from cleanrr.agent import Agent
from cleanrr.config import Settings
from cleanrr.identity import Identity
from cleanrr.permissions import CALLBACK_PREFIX
from cleanrr.tools._user_request import _resolve_user_id

logger = logging.getLogger(__name__)

AGENT_KEY = "agent"
IDENTITY_KEY = "identity"
SETTINGS_KEY = "settings"

# Telegram's sendMessage hard cap (core.telegram.org/bots/api#sendmessage):
# 1-4096 UTF-16 code units. PTB doesn't split or truncate for you.
_TELEGRAM_MAX_REPLY_CHARS = 4096


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    metrics.telegram_messages_total.labels(kind="command", command="start").inc()
    await update.message.reply_text(
        "cleanrr is online. Ask about your requests, or ask me to cancel or "
        "re-search one — I'll confirm before doing anything destructive."
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

    # username is user-controlled; strip non-printable chars to prevent log injection.
    safe_username = "".join(c for c in (user.username or "?") if c.isprintable())[:32]
    logger.info("message from %s (id=%s): %s", safe_username, user.id, text[:80])

    start = time.perf_counter()
    try:
        reply = await agent.respond(telegram_user_id=user.id, prompt=text)
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

    text = reply or "(no reply)"
    if len(text) > _TELEGRAM_MAX_REPLY_CHARS:
        text = text[: _TELEGRAM_MAX_REPLY_CHARS - 1] + "…"

    try:
        await update.message.reply_text(text)
    except TelegramError:
        logger.warning("failed to deliver reply to user %s", user.id, exc_info=True)
        metrics.claude_requests_total.labels(status="delivery_failed").inc()
        return
    metrics.claude_requests_total.labels(status="success").inc()


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

    agent: Agent = context.application.bot_data[AGENT_KEY]
    overseerr_client = agent.overseerr_client
    if (
        settings.overseerr_url is None
        or settings.overseerr_api_key is None
        or overseerr_client is None
    ):
        await update.message.reply_text(
            "Overseerr isn't configured yet — ask the admin to set "
            "OVERSEERR_URL and OVERSEERR_API_KEY."
        )
        return

    overseerr_username = args[0].lstrip("@")
    base_url = str(settings.overseerr_url).rstrip("/")
    _user_id, resolve_status = await _resolve_user_id(
        overseerr_client, base_url, overseerr_username
    )
    if resolve_status == "user_not_found":
        await update.message.reply_text(
            f"Couldn't find an Overseerr user named '{overseerr_username}' — "
            "check the username and try again."
        )
        return
    if resolve_status == "http_error":
        await update.message.reply_text(
            "Couldn't reach Overseerr to verify that user — try again in a moment."
        )
        return
    if resolve_status == "parse_error":
        await update.message.reply_text("Unexpected response from Overseerr — try again later.")
        return

    identity: Identity = context.application.bot_data[IDENTITY_KEY]
    code = await identity.issue_code(overseerr_username)
    await update.message.reply_text(
        f"Link code for @{overseerr_username}: {code}\n"
        f"Expires in {settings.link_code_ttl_hours}h. Share it; they DM me /link {code}."
    )


async def _safe_answer(query: CallbackQuery, *args: Any, **kwargs: Any) -> None:
    """answerCallbackQuery fails if the query is stale (Telegram invalidates
    it after a short window) — that's a routine race, not a bug to crash on."""
    try:
        await query.answer(*args, **kwargs)
    except TelegramError:
        logger.warning("failed to answer callback query", exc_info=True)


async def on_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return

    parts = query.data.split(":")
    if len(parts) != 4 or f"{parts[0]}:{parts[1]}:" != CALLBACK_PREFIX:
        logger.warning("malformed confirmation callback_data")
        await _safe_answer(query)
        return
    confirmation_id, decision = parts[2], parts[3]
    if decision not in ("yes", "no"):
        logger.warning("confirmation callback with unknown decision: %s", decision)
        await _safe_answer(query)
        return

    agent: Agent = context.application.bot_data[AGENT_KEY]
    registry = agent.confirmation_registry
    if registry is None:
        await _safe_answer(query)
        return

    pending = await registry.get(confirmation_id)
    if pending is None:
        await _safe_answer(query)
        try:
            await query.edit_message_text("This confirmation has expired.")
        except Exception:
            logger.debug("couldn't edit expired confirmation message", exc_info=True)
        return

    # answerCallbackQuery only accepts ONE response per query; calling it
    # unconditionally up front would silently swallow this alert.
    if update.effective_user.id != pending.telegram_user_id:
        await _safe_answer(query, "This confirmation isn't for you.", show_alert=True)
        return

    await _safe_answer(query)
    await registry.resolve(
        confirmation_id,
        telegram_user_id=update.effective_user.id,
        allowed=(decision == "yes"),
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


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Backstop for exceptions PTB's per-handler try/excepts don't cover
    (e.g. a reply_text call itself raising Forbidden/BadRequest). Without
    this registered, PTB only logs and drops the update — no visibility."""
    logger.error("unhandled exception processing update %r", update, exc_info=context.error)
