"""The ``can_use_tool`` permission callback wired to Telegram confirmation prompts."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

import cleanrr.metrics as metrics
from cleanrr.config import Settings
from cleanrr.permissions._formatters import ConfirmationFormatter
from cleanrr.permissions._registry import ConfirmationRegistry, Outcome
from cleanrr.tools._context import current_telegram_user_id

logger = logging.getLogger(__name__)

WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "remove_my_request",
        "delete_torrent",
        "force_research_movie",
        "force_research_show",
    }
)

# CALLBACK_PREFIX uses ':' as a separator. The confirmation_id segment comes
# from secrets.token_urlsafe(), which encodes to the RFC 4648 §5 URL-safe
# alphabet [A-Za-z0-9_-] — no ':' — so split(':') stays unambiguous.
CALLBACK_PREFIX = "cleanrr:confirm:"

CanUseTool = Callable[
    [str, dict[str, Any], ToolPermissionContext],
    Awaitable[PermissionResultAllow | PermissionResultDeny],
]


def make_can_use_tool(
    telegram_bot: Bot,
    registry: ConfirmationRegistry,
    settings: Settings,
    formatters: dict[str, ConfirmationFormatter],
) -> CanUseTool:
    """Build the ``can_use_tool`` callback wired to a specific Telegram bot + registry."""

    async def _resolve_prompt_text(tool_name: str, tool_args: dict[str, Any]) -> str:
        # Each formatter is responsible for its own timeout (see the remove_my_request
        # formatter for the pattern). The catch-all here covers programmer error in
        # a future formatter, not slow I/O.
        formatter = formatters.get(tool_name)
        generic = f"Run {tool_name}?"
        if formatter is None:
            return generic
        try:
            return await formatter(tool_args)
        except Exception:
            logger.exception("confirmation formatter crashed for %s", tool_name)
            return generic

    async def can_use_tool(
        tool_name: str,
        input_data: dict[str, Any],
        _context: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        # MCP tool names arrive prefixed (e.g. "mcp__cleanrr__remove_my_request");
        # the WRITE_TOOLS set holds bare names, so check by suffix.
        bare_name = tool_name.rsplit("__", 1)[-1]
        if bare_name not in WRITE_TOOLS:
            return PermissionResultAllow(updated_input=input_data)

        try:
            telegram_user_id = current_telegram_user_id.get()
        except LookupError:
            logger.error("can_use_tool fired without a telegram user contextvar")
            return PermissionResultDeny(message="internal error: no caller context")

        confirmation_id = await registry.reserve(
            tool_name=bare_name, telegram_user_id=telegram_user_id
        )
        if confirmation_id is None:
            metrics.destructive_actions_total.labels(tool=bare_name, outcome="denied").inc()
            return PermissionResultDeny(message="confirmation registry full")

        prompt_text = await _resolve_prompt_text(bare_name, input_data)
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Confirm",
                        callback_data=f"{CALLBACK_PREFIX}{confirmation_id}:yes",
                    ),
                    InlineKeyboardButton(
                        "Cancel",
                        callback_data=f"{CALLBACK_PREFIX}{confirmation_id}:no",
                    ),
                ]
            ]
        )

        try:
            sent_message = await telegram_bot.send_message(
                chat_id=telegram_user_id, text=prompt_text, reply_markup=keyboard
            )
        except TelegramError:
            logger.exception("failed to send confirmation prompt for %s", bare_name)
            metrics.destructive_actions_total.labels(tool=bare_name, outcome="denied").inc()
            return PermissionResultDeny(message="couldn't send confirmation prompt")

        pending = await registry.register(
            confirmation_id=confirmation_id,
            telegram_user_id=telegram_user_id,
            tool_name=bare_name,
            tool_args=input_data,
            prompt_message_id=sent_message.message_id,
        )

        try:
            allowed = await asyncio.wait_for(
                pending.future, timeout=settings.confirmation_ttl_seconds
            )
        except TimeoutError:
            await registry.timeout(pending.confirmation_id)
            allowed = False
        # Read outcome from the entry rather than inferring from the bool. The
        # sweeper and timeout() also resolve to False, but the outcome distinguishes
        # them from a user-cancel click.
        outcome: Outcome = pending.outcome or "timed_out"
        metrics.destructive_actions_total.labels(tool=bare_name, outcome=outcome).inc()
        outcome_text = {
            "confirmed": "Confirmed.",
            "denied": "Cancelled.",
            "timed_out": "Timed out.",
        }[outcome]
        await _edit_outcome(telegram_bot, telegram_user_id, sent_message.message_id, outcome_text)
        if allowed:
            return PermissionResultAllow(updated_input=input_data)
        return PermissionResultDeny(
            message="confirmation timed out" if outcome == "timed_out" else "user declined"
        )

    return can_use_tool


async def _edit_outcome(telegram_bot: Bot, chat_id: int, message_id: int, text: str) -> None:
    try:
        await telegram_bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)
    except TelegramError:
        logger.warning("failed to edit confirmation outcome message", exc_info=True)
