"""In-memory store of pending destructive-action confirmations."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

# Single source of truth for the destructive_actions_total{outcome=...} label.
# Prometheus does not validate label values at runtime, so any code stamping the
# metric with a value outside this literal will silently inflate cardinality.
# Pre-confirmation guards (admin gates, ownership checks) belong on
# tool_calls_total{status=...}, NOT this counter.
Outcome = Literal["confirmed", "denied", "timed_out"]

_REGISTRY_MAX_ENTRIES = 100
_REGISTRY_MAX_PER_USER = 3


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
