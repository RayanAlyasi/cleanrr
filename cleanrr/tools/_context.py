from __future__ import annotations

from typing import overload

_UNSET = object()


class _CurrentTelegramUserId:
    """Tracks the Telegram user ID for the in-flight request.

    NOT a contextvars.ContextVar: MCP tool calls run on the SDK's persistent
    background read-loop task, spawned once when the client connects — not as
    a child of the task that calls Agent.respond(). A ContextVar set inside
    respond() is invisible there. Plain mutable state works instead because
    Agent.respond() holds a single asyncio.Lock for the whole process, so only
    one request is ever in flight at a time.
    """

    def __init__(self) -> None:
        self._value: int | None = None

    def set(self, value: int) -> int | None:
        previous = self._value
        self._value = value
        return previous

    @overload
    def get(self) -> int: ...
    @overload
    def get(self, default: int | None) -> int | None: ...
    def get(self, default: object = _UNSET) -> int | None:
        if self._value is not None:
            return self._value
        if default is _UNSET:
            raise LookupError("current_telegram_user_id is not set")
        return default  # type: ignore[return-value]

    def reset(self, token: int | None) -> None:
        self._value = token


current_telegram_user_id = _CurrentTelegramUserId()
