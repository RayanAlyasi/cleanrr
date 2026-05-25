"""Destructive-action confirmation flow.

When Claude calls a tool listed in ``WRITE_TOOLS``, the SDK's ``can_use_tool``
callback intercepts the call, posts a Telegram message with Confirm/Cancel
buttons, suspends until the user clicks (or the TTL elapses), and returns
``PermissionResultAllow`` / ``PermissionResultDeny`` accordingly. Read-only
tools allow immediately without sending a message.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

import cleanrr.metrics as metrics
from cleanrr.config import Settings
from cleanrr.tools._context import current_telegram_user_id

logger = logging.getLogger(__name__)

# Single source of truth for the destructive_actions_total{outcome=...} label.
# Prometheus does not validate label values at runtime, so any code stamping the
# metric with a value outside this literal will silently inflate cardinality.
# Pre-confirmation guards (admin gates, ownership checks) belong on
# tool_calls_total{status=...}, NOT this counter.
Outcome = Literal["confirmed", "denied", "timed_out"]

WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "remove_my_request",
        "delete_torrent",
        "force_research_movie",
        "force_research_show",
    }
)

_REGISTRY_MAX_ENTRIES = 100
_REGISTRY_MAX_PER_USER = 3
_FORMATTER_TIMEOUT_SECONDS = 1.5

# CALLBACK_PREFIX uses ':' as a separator. The confirmation_id segment comes
# from secrets.token_urlsafe(), which encodes to the RFC 4648 §5 URL-safe
# alphabet [A-Za-z0-9_-] — no ':' — so split(':') stays unambiguous.
CALLBACK_PREFIX = "cleanrr:confirm:"


@dataclass
class PendingConfirmation:
    confirmation_id: str
    telegram_user_id: int
    tool_name: str
    tool_args: dict[str, Any]
    created_at: float
    prompt_message_id: int
    future: asyncio.Future[bool] = field(repr=False)
    # Outcome label is stamped by whichever path resolves the future, so a race
    # between the sweeper and wait_for can't misclassify a timeout as a user
    # cancel (the future's bool result is the same in both cases).
    outcome: Outcome | None = None


class ConfirmationRegistry:
    """In-memory store of pending confirmations awaiting a button click.

    Keyed by an unguessable ``confirmation_id`` carried in the inline keyboard's
    ``callback_data``. Bounded to ``_REGISTRY_MAX_ENTRIES`` to cap the blast
    radius of any client-side or buggy producer.
    """

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl_seconds = ttl_seconds
        self._entries: dict[str, PendingConfirmation] = {}
        self._lock = asyncio.Lock()
        self._sweeper_task: asyncio.Task[None] | None = None

    @property
    def ttl_seconds(self) -> float:
        return self._ttl_seconds

    async def start(self) -> None:
        if self._sweeper_task is not None:
            return
        self._sweeper_task = asyncio.create_task(self._sweep_loop())

    async def stop(self) -> None:
        task = self._sweeper_task
        self._sweeper_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("confirmation registry sweeper crashed on shutdown")

    async def reserve(self, *, tool_name: str, telegram_user_id: int) -> str | None:
        """Reserve a new confirmation_id if the registry has room. Returns None if full.

        The caller fills in ``prompt_message_id`` via ``register()`` once the
        Telegram message is sent. Enforces a per-user cap so a single noisy
        client can't exhaust the global slots.
        """
        async with self._lock:
            self._evict_expired_locked()
            if len(self._entries) >= _REGISTRY_MAX_ENTRIES:
                logger.warning(
                    "confirmation registry full (%d entries); refusing new prompt for %s",
                    _REGISTRY_MAX_ENTRIES,
                    tool_name,
                )
                return None
            user_entries = sum(
                1 for p in self._entries.values() if p.telegram_user_id == telegram_user_id
            )
            if user_entries >= _REGISTRY_MAX_PER_USER:
                logger.warning(
                    "user %s at per-user confirmation cap (%d); refusing %s",
                    telegram_user_id,
                    _REGISTRY_MAX_PER_USER,
                    tool_name,
                )
                return None
            return secrets.token_urlsafe(16)

    async def register(
        self,
        *,
        confirmation_id: str,
        telegram_user_id: int,
        tool_name: str,
        tool_args: dict[str, Any],
        prompt_message_id: int,
    ) -> PendingConfirmation:
        async with self._lock:
            pending = PendingConfirmation(
                confirmation_id=confirmation_id,
                telegram_user_id=telegram_user_id,
                tool_name=tool_name,
                tool_args=tool_args,
                created_at=time.monotonic(),
                prompt_message_id=prompt_message_id,
                future=asyncio.get_running_loop().create_future(),
            )
            self._entries[confirmation_id] = pending
            return pending

    async def get(self, confirmation_id: str) -> PendingConfirmation | None:
        async with self._lock:
            self._evict_expired_locked()
            return self._entries.get(confirmation_id)

    async def resolve(
        self,
        confirmation_id: str,
        *,
        telegram_user_id: int,
        allowed: bool,
    ) -> bool:
        """Resolve a pending confirmation. Returns True if the future was set.

        Mismatched ``telegram_user_id`` does NOT resolve the future — the rightful
        owner can still click their own button. The bot.py callback handler is
        expected to surface a "this isn't for you" message in that case.
        """
        async with self._lock:
            pending = self._entries.get(confirmation_id)
            if pending is None:
                return False
            if pending.telegram_user_id != telegram_user_id:
                return False
            if pending.future.done():
                return False
            pending.outcome = "confirmed" if allowed else "denied"
            pending.future.set_result(allowed)
            del self._entries[confirmation_id]
        return True

    async def timeout(self, confirmation_id: str) -> None:
        """Mark a pending confirmation as timed out and resolve its future with False."""
        async with self._lock:
            pending = self._entries.pop(confirmation_id, None)
        if pending is not None and not pending.future.done():
            pending.outcome = "timed_out"
            pending.future.set_result(False)

    async def _sweep_loop(self) -> None:
        interval = max(self._ttl_seconds / 2, 1.0)
        try:
            while True:
                await asyncio.sleep(interval)
                async with self._lock:
                    self._evict_expired_locked()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("confirmation registry sweeper crashed")

    def _evict_expired_locked(self) -> None:
        now = time.monotonic()
        expired_ids = [
            cid
            for cid, pending in self._entries.items()
            if (now - pending.created_at) > self._ttl_seconds
        ]
        for cid in expired_ids:
            pending = self._entries.pop(cid)
            if not pending.future.done():
                pending.outcome = "timed_out"
                pending.future.set_result(False)


ConfirmationFormatter = Callable[[dict[str, Any]], Awaitable[str]]


def _build_remove_my_request_formatter(
    overseerr_client: httpx.AsyncClient | None,
    settings: Settings,
) -> ConfirmationFormatter:
    """Formatter that enriches the confirmation prompt with the request title and status."""

    async def formatter(tool_args: dict[str, Any]) -> str:
        request_id = tool_args.get("request_id")
        fallback = f"Cancel Overseerr request #{request_id}?"
        if overseerr_client is None or settings.overseerr_url is None or request_id is None:
            return fallback
        base_url = str(settings.overseerr_url).rstrip("/")
        try:
            resp = await asyncio.wait_for(
                overseerr_client.get(f"{base_url}/api/v1/request/{request_id}"),
                timeout=_FORMATTER_TIMEOUT_SECONDS,
            )
        except (TimeoutError, httpx.HTTPError):
            return fallback
        if resp.status_code != 200:
            return fallback
        try:
            data = resp.json()
        except ValueError:
            return fallback
        media = data.get("media") or {}
        # Overseerr title/name fields come from external metadata and may be
        # arbitrarily long; cap so a hostile entry can't blow past Telegram's
        # 4096-char message limit.
        title = str(media.get("title") or media.get("name") or "Unknown")[:80]
        media_type = str(media.get("mediaType") or "media")[:20]
        status_label = _request_status_label(data.get("status"))
        return (
            f"Cancel request: {title} ({media_type}, status: {status_label})? "
            "This removes the Overseerr request only — it does not delete "
            "already-downloaded media."
        )

    return formatter


_OVERSEERR_REQUEST_STATUS_LABELS = {
    1: "pending",
    2: "approved",
    3: "declined",
}


def _request_status_label(status: object) -> str:
    if isinstance(status, int):
        return _OVERSEERR_REQUEST_STATUS_LABELS.get(status, f"status {status}")
    return "unknown"


def _format_bytes(size: object) -> str:
    if not isinstance(size, int | float) or size < 0:
        return "?"
    if size >= 1_073_741_824:
        return f"{size / 1_073_741_824:.1f} GB"
    if size >= 1_048_576:
        return f"{size / 1_048_576:.0f} MB"
    return f"{int(size)} B"


def _build_delete_torrent_formatter(
    qbit_client: httpx.AsyncClient | None,
    settings: Settings,
) -> ConfirmationFormatter:
    """Look up the torrent name + size to make the confirmation prompt meaningful."""

    async def formatter(tool_args: dict[str, Any]) -> str:
        raw = tool_args.get("torrent_hash")
        # Mirror the tool's own normalization (strip + lower) BEFORE validating,
        # so the prompt's "invalid" verdict matches what the tool will actually do.
        # Otherwise a hash with stray whitespace renders as "invalid" but then
        # passes the tool's check, and the confirmation prompt becomes a lie.
        torrent_hash = raw.strip() if isinstance(raw, str) else raw
        if (
            not isinstance(torrent_hash, str)
            or len(torrent_hash) != 40
            or not all(c in "0123456789abcdefABCDEF" for c in torrent_hash)
        ):
            shown = (
                torrent_hash[:40] + "..."
                if isinstance(torrent_hash, str) and torrent_hash
                else "<missing>"
            )
            return f"Delete torrent (invalid hash: {shown}) AND its files? Tool will refuse."
        normalized = torrent_hash.lower()
        fallback = f"Delete torrent {normalized} AND its files from disk? This cannot be undone."
        if qbit_client is None or settings.qbittorrent_url is None:
            return fallback
        base_url = str(settings.qbittorrent_url).rstrip("/")
        # The qBit session may not be live here (the read tools log in lazily).
        # If unauthenticated we just fall back rather than try to log in from
        # the formatter path.
        try:
            resp = await asyncio.wait_for(
                qbit_client.get(f"{base_url}/api/v2/torrents/info", params={"hashes": normalized}),
                timeout=_FORMATTER_TIMEOUT_SECONDS,
            )
        except (TimeoutError, httpx.HTTPError):
            return fallback
        if resp.status_code != 200:
            return fallback
        try:
            data = resp.json()
        except ValueError:
            return fallback
        if not isinstance(data, list) or not data:
            return fallback
        entry = data[0]
        if not isinstance(entry, dict):
            return fallback
        name = str(entry.get("name") or "unknown")[:80]
        size = _format_bytes(entry.get("size"))
        return (
            f"Delete torrent '{name}' ({size}) AND its downloaded files from disk? "
            "This cannot be undone."
        )

    return formatter


def _build_force_research_movie_formatter() -> ConfirmationFormatter:
    async def formatter(tool_args: dict[str, Any]) -> str:
        title = str(tool_args.get("title") or "")[:80] or "your movie"
        return f"Re-search Radarr for '{title}'?"

    return formatter


def _build_force_research_show_formatter() -> ConfirmationFormatter:
    async def formatter(tool_args: dict[str, Any]) -> str:
        title = str(tool_args.get("title") or "")[:80] or "your show"
        return f"Re-search Sonarr for '{title}' (whole series)?"

    return formatter


CanUseTool = Callable[
    [str, dict[str, Any], ToolPermissionContext],
    Awaitable[PermissionResultAllow | PermissionResultDeny],
]


def build_confirmation_formatters(
    overseerr_client: httpx.AsyncClient | None,
    qbit_client: httpx.AsyncClient | None,
    settings: Settings,
) -> dict[str, ConfirmationFormatter]:
    return {
        "remove_my_request": _build_remove_my_request_formatter(overseerr_client, settings),
        "delete_torrent": _build_delete_torrent_formatter(qbit_client, settings),
        "force_research_movie": _build_force_research_movie_formatter(),
        "force_research_show": _build_force_research_show_formatter(),
    }


def make_can_use_tool(
    telegram_bot: Any,
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


async def _edit_outcome(telegram_bot: Any, chat_id: int, message_id: int, text: str) -> None:
    try:
        await telegram_bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)
    except TelegramError:
        logger.warning("failed to edit confirmation outcome message", exc_info=True)
