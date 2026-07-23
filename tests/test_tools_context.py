from __future__ import annotations

import asyncio

import pytest

from cleanrr.tools._context import current_telegram_user_id


@pytest.mark.asyncio
async def test_reset_after_respond() -> None:
    """Verify the value is cleared after respond() returns."""
    token = current_telegram_user_id.set(42)
    current_telegram_user_id.reset(token)

    with pytest.raises(LookupError):
        current_telegram_user_id.get()


def test_get_returns_default_when_unset() -> None:
    assert current_telegram_user_id.get(None) is None


@pytest.mark.asyncio
async def test_visible_from_a_task_created_before_set() -> None:
    """A value set after a task starts must still be visible inside that task.

    A contextvars.ContextVar does NOT have this property — a task created
    before .set() runs keeps whatever context existed at its own creation.
    That gap is exactly what broke real tool calls: the SDK dispatches them
    from a background read-loop task spawned once at connect time, before
    any per-request .set() call.
    """
    ready = asyncio.Event()
    seen: list[int] = []

    async def _pre_existing_reader() -> None:
        await ready.wait()
        seen.append(current_telegram_user_id.get())

    reader_task = asyncio.create_task(_pre_existing_reader())
    await asyncio.sleep(0)  # let the task start and block on ready.wait()

    token = current_telegram_user_id.set(777)
    try:
        ready.set()
        await reader_task
    finally:
        current_telegram_user_id.reset(token)

    assert seen == [777]
