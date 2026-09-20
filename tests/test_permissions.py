from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

import cleanrr.metrics
from cleanrr.config import Settings
from cleanrr.permissions import (
    WRITE_TOOLS,
    ConfirmationRegistry,
    build_confirmation_formatters,
    make_can_use_tool,
)
from cleanrr.permissions._callback import ADMIN_ONLY_TOOLS
from cleanrr.permissions._formatters import _format_bytes, _request_status_label


def _settings(ttl: float = 60.0, admin_ids: set[int] | None = None) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="t",  # type: ignore[arg-type]
        anthropic_api_key="sk",  # type: ignore[arg-type]
        confirmation_ttl_seconds=ttl,
        overseerr_url="http://overseerr:5055",  # type: ignore[arg-type]
        overseerr_api_key="key",  # type: ignore[arg-type]
        admin_telegram_ids=admin_ids or set(),
    )


def _make_bot() -> MagicMock:
    """Bot mock that returns a sent-message with a stable message_id."""
    bot = MagicMock()
    sent = MagicMock()
    sent.message_id = 999
    bot.send_message = AsyncMock(return_value=sent)
    bot.edit_message_text = AsyncMock()
    bot.edit_message_reply_markup = AsyncMock()
    return bot


def _counter(tool: str, outcome: str) -> float:
    return cleanrr.metrics.destructive_actions_total.labels(tool=tool, outcome=outcome)._value.get()


def _tool_calls_counter(tool: str, status: str) -> float:
    return cleanrr.metrics.tool_calls_total.labels(tool=tool, status=status)._value.get()


# ---------------------------------------------------------------------------
# ConfirmationRegistry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reserve_and_register_returns_pending_entry() -> None:
    reg = ConfirmationRegistry(ttl_seconds=60)
    cid = await reg.reserve(tool_name="remove_my_request", telegram_user_id=1)
    assert cid is not None

    pending = await reg.register(
        confirmation_id=cid,
        telegram_user_id=42,
        tool_name="remove_my_request",
        tool_args={"request_id": 7},
        prompt_message_id=100,
    )
    assert pending.confirmation_id == cid
    assert pending.telegram_user_id == 42
    fetched = await reg.get(cid)
    assert fetched is pending


@pytest.mark.asyncio
async def test_resolve_sets_future_result_for_right_user() -> None:
    reg = ConfirmationRegistry(ttl_seconds=60)
    cid = await reg.reserve(tool_name="remove_my_request", telegram_user_id=1)
    assert cid is not None
    pending = await reg.register(
        confirmation_id=cid,
        telegram_user_id=42,
        tool_name="remove_my_request",
        tool_args={},
        prompt_message_id=1,
    )

    ok = await reg.resolve(cid, telegram_user_id=42, allowed=True)
    assert ok is True
    assert pending.future.result() is True
    # Entry removed after resolve
    assert await reg.get(cid) is None


@pytest.mark.asyncio
async def test_resolve_ignores_wrong_user() -> None:
    reg = ConfirmationRegistry(ttl_seconds=60)
    cid = await reg.reserve(tool_name="remove_my_request", telegram_user_id=1)
    assert cid is not None
    pending = await reg.register(
        confirmation_id=cid,
        telegram_user_id=42,
        tool_name="remove_my_request",
        tool_args={},
        prompt_message_id=1,
    )

    ok = await reg.resolve(cid, telegram_user_id=999, allowed=True)
    assert ok is False
    assert not pending.future.done()
    # Original user can still resolve
    ok2 = await reg.resolve(cid, telegram_user_id=42, allowed=False)
    assert ok2 is True
    assert pending.future.result() is False


@pytest.mark.asyncio
async def test_timeout_resolves_with_false_and_removes_entry() -> None:
    reg = ConfirmationRegistry(ttl_seconds=60)
    cid = await reg.reserve(tool_name="remove_my_request", telegram_user_id=1)
    assert cid is not None
    pending = await reg.register(
        confirmation_id=cid,
        telegram_user_id=42,
        tool_name="remove_my_request",
        tool_args={},
        prompt_message_id=1,
    )

    await reg.timeout(cid)
    assert pending.future.done()
    assert pending.future.result() is False
    assert await reg.get(cid) is None


@pytest.mark.asyncio
async def test_registry_full_returns_none() -> None:
    reg = ConfirmationRegistry(ttl_seconds=60)
    # Fill to global capacity — use distinct user_ids so per-user cap doesn't fire first.
    reserved: list[str] = []
    for i in range(100):
        cid = await reg.reserve(tool_name="remove_my_request", telegram_user_id=i)
        assert cid is not None
        await reg.register(
            confirmation_id=cid,
            telegram_user_id=i,
            tool_name="remove_my_request",
            tool_args={},
            prompt_message_id=1,
        )
        reserved.append(cid)

    overflow = await reg.reserve(tool_name="remove_my_request", telegram_user_id=9999)
    assert overflow is None

    # Resolving one frees a slot for that user_id
    await reg.resolve(reserved[0], telegram_user_id=0, allowed=False)
    new = await reg.reserve(tool_name="remove_my_request", telegram_user_id=9999)
    assert new is not None


@pytest.mark.asyncio
async def test_per_user_cap_blocks_single_user_from_exhausting() -> None:
    reg = ConfirmationRegistry(ttl_seconds=60)
    for _ in range(3):
        cid = await reg.reserve(tool_name="remove_my_request", telegram_user_id=42)
        assert cid is not None
        await reg.register(
            confirmation_id=cid,
            telegram_user_id=42,
            tool_name="remove_my_request",
            tool_args={},
            prompt_message_id=1,
        )

    # 4th from same user is blocked
    blocked = await reg.reserve(tool_name="remove_my_request", telegram_user_id=42)
    assert blocked is None

    # A different user can still reserve
    other = await reg.reserve(tool_name="remove_my_request", telegram_user_id=99)
    assert other is not None


@pytest.mark.asyncio
async def test_concurrent_confirmations_same_user_have_distinct_ids() -> None:
    reg = ConfirmationRegistry(ttl_seconds=60)
    cid_a = await reg.reserve(tool_name="remove_my_request", telegram_user_id=1)
    cid_b = await reg.reserve(tool_name="remove_my_request", telegram_user_id=1)
    assert cid_a is not None and cid_b is not None
    assert cid_a != cid_b


@pytest.mark.asyncio
async def test_lazy_expiration_evicts_old_entries() -> None:
    reg = ConfirmationRegistry(ttl_seconds=0.01)
    cid = await reg.reserve(tool_name="remove_my_request", telegram_user_id=1)
    assert cid is not None
    await reg.register(
        confirmation_id=cid,
        telegram_user_id=1,
        tool_name="remove_my_request",
        tool_args={},
        prompt_message_id=1,
    )
    await asyncio.sleep(0.05)
    # get() triggers lazy eviction
    assert await reg.get(cid) is None


@pytest.mark.asyncio
async def test_sweeper_task_starts_and_stops() -> None:
    reg = ConfirmationRegistry(ttl_seconds=60)
    await reg.start()
    await reg.stop()


@pytest.mark.asyncio
async def test_sweeper_start_twice_is_a_noop() -> None:
    reg = ConfirmationRegistry(ttl_seconds=60)
    await reg.start()
    task = reg._sweeper_task
    await reg.start()  # must not replace the running task
    assert reg._sweeper_task is task
    await reg.stop()


@pytest.mark.asyncio
async def test_stop_survives_sweeper_crash_on_cancel() -> None:
    """A sweeper task that raises something other than CancelledError while
    being torn down must not propagate out of stop() — shutdown must finish."""
    reg = ConfirmationRegistry(ttl_seconds=60)

    # Don't call reg.start() — swap in a task that reacts to cancellation by
    # raising instead of propagating CancelledError, simulating a sweeper
    # that crashes mid-teardown.
    async def _crash_instead_of_cancelling() -> None:
        try:
            await asyncio.sleep(999)
        except asyncio.CancelledError:
            raise RuntimeError("boom") from None

    reg._sweeper_task = asyncio.ensure_future(_crash_instead_of_cancelling())
    await asyncio.sleep(0)  # let it start awaiting

    await reg.stop()  # must not raise


@pytest.mark.asyncio
async def test_sweep_loop_logs_and_survives_internal_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected exception inside the sweep loop's own eviction pass
    must be logged, not silently kill the background task."""
    reg = ConfirmationRegistry(ttl_seconds=0.01)  # sweep interval floors at 1.0s regardless

    call_count = 0
    original = reg._evict_expired_locked

    def _flaky_evict() -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("boom")
        original()

    monkeypatch.setattr(reg, "_evict_expired_locked", _flaky_evict)

    await reg.start()
    for _ in range(60):
        if call_count >= 1:
            break
        await asyncio.sleep(0.05)
    await reg.stop()

    assert call_count >= 1


@pytest.mark.asyncio
async def test_has_pending_for_user() -> None:
    reg = ConfirmationRegistry(ttl_seconds=60)
    assert await reg.has_pending_for_user(1) is False

    cid = await reg.reserve(tool_name="remove_my_request", telegram_user_id=1)
    assert cid is not None
    await reg.register(
        confirmation_id=cid,
        telegram_user_id=1,
        tool_name="remove_my_request",
        tool_args={},
        prompt_message_id=1,
    )
    assert await reg.has_pending_for_user(1) is True
    assert await reg.has_pending_for_user(2) is False


@pytest.mark.asyncio
async def test_has_pending_for_user_false_once_expired() -> None:
    reg = ConfirmationRegistry(ttl_seconds=0.01)
    cid = await reg.reserve(tool_name="remove_my_request", telegram_user_id=1)
    assert cid is not None
    await reg.register(
        confirmation_id=cid,
        telegram_user_id=1,
        tool_name="remove_my_request",
        tool_args={},
        prompt_message_id=1,
    )
    await asyncio.sleep(0.05)
    assert await reg.has_pending_for_user(1) is False


@pytest.mark.asyncio
async def test_cancel_for_user_resolves_future_as_denied() -> None:
    reg = ConfirmationRegistry(ttl_seconds=60)
    cid = await reg.reserve(tool_name="remove_my_request", telegram_user_id=42)
    assert cid is not None
    pending = await reg.register(
        confirmation_id=cid,
        telegram_user_id=42,
        tool_name="remove_my_request",
        tool_args={},
        prompt_message_id=1,
    )

    count = await reg.cancel_for_user(42)

    assert count == 1
    assert pending.outcome == "denied"
    assert pending.future.result() is False
    assert await reg.get(cid) is None


@pytest.mark.asyncio
async def test_cancel_for_user_resolves_all_entries_for_that_user() -> None:
    reg = ConfirmationRegistry(ttl_seconds=60)
    cid_a = await reg.reserve(tool_name="remove_my_request", telegram_user_id=42)
    cid_b = await reg.reserve(tool_name="remove_my_request", telegram_user_id=42)
    cid_other = await reg.reserve(tool_name="remove_my_request", telegram_user_id=99)
    assert cid_a is not None
    assert cid_b is not None
    assert cid_other is not None
    for cid, uid in ((cid_a, 42), (cid_b, 42), (cid_other, 99)):
        await reg.register(
            confirmation_id=cid,
            telegram_user_id=uid,
            tool_name="remove_my_request",
            tool_args={},
            prompt_message_id=1,
        )

    count = await reg.cancel_for_user(42)

    assert count == 2
    assert await reg.has_pending_for_user(99) is True


@pytest.mark.asyncio
async def test_cancel_for_user_twice_is_idempotent() -> None:
    reg = ConfirmationRegistry(ttl_seconds=60)
    cid = await reg.reserve(tool_name="remove_my_request", telegram_user_id=42)
    assert cid is not None
    await reg.register(
        confirmation_id=cid,
        telegram_user_id=42,
        tool_name="remove_my_request",
        tool_args={},
        prompt_message_id=1,
    )

    first = await reg.cancel_for_user(42)
    second = await reg.cancel_for_user(42)

    assert first == 1
    assert second == 0


@pytest.mark.asyncio
async def test_cancel_for_user_skips_already_resolved_entry() -> None:
    reg = ConfirmationRegistry(ttl_seconds=60)
    cid = await reg.reserve(tool_name="remove_my_request", telegram_user_id=42)
    assert cid is not None
    pending = await reg.register(
        confirmation_id=cid,
        telegram_user_id=42,
        tool_name="remove_my_request",
        tool_args={},
        prompt_message_id=1,
    )
    await reg.resolve(cid, telegram_user_id=42, allowed=True)

    count = await reg.cancel_for_user(42)

    assert count == 0
    assert pending.outcome == "confirmed"
    assert pending.future.result() is True


@pytest.mark.asyncio
async def test_cancel_for_user_with_nothing_pending_returns_zero() -> None:
    reg = ConfirmationRegistry(ttl_seconds=60)
    assert await reg.cancel_for_user(42) == 0


# ---------------------------------------------------------------------------
# make_can_use_tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_tool_allows_immediately_without_telegram_message() -> None:
    bot = _make_bot()
    reg = ConfirmationRegistry(ttl_seconds=60)
    settings = _settings()
    cb = make_can_use_tool(bot, reg, settings, formatters={}, telegram_user_id=42)

    result = await cb("mcp__cleanrr__list_my_requests", {"foo": "bar"}, MagicMock())

    assert isinstance(result, PermissionResultAllow)
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_tool_with_confirm_returns_allow_and_increments_metric() -> None:
    bot = _make_bot()
    reg = ConfirmationRegistry(ttl_seconds=60)
    settings = _settings()
    cb = make_can_use_tool(bot, reg, settings, formatters={}, telegram_user_id=42)

    before = _counter("remove_my_request", "confirmed")

    async def _resolve_after_send() -> None:
        # Wait for can_use_tool to register the pending confirmation
        for _ in range(50):
            await asyncio.sleep(0.01)
            # The pending registry has at most one entry under this test
            async with reg._lock:  # type: ignore[attr-defined]
                if reg._entries:  # type: ignore[attr-defined]
                    cid = next(iter(reg._entries))  # type: ignore[attr-defined]
                    break
        else:
            raise AssertionError("no pending confirmation appeared")
        await reg.resolve(cid, telegram_user_id=42, allowed=True)

    results = await asyncio.gather(
        cb("mcp__cleanrr__remove_my_request", {"request_id": 7}, MagicMock()),
        _resolve_after_send(),
    )

    result = results[0]
    assert isinstance(result, PermissionResultAllow)
    bot.send_message.assert_awaited_once()
    assert _counter("remove_my_request", "confirmed") == before + 1


@pytest.mark.asyncio
async def test_write_tool_with_cancel_returns_deny_and_increments_metric() -> None:
    bot = _make_bot()
    reg = ConfirmationRegistry(ttl_seconds=60)
    settings = _settings()
    cb = make_can_use_tool(bot, reg, settings, formatters={}, telegram_user_id=42)

    before = _counter("remove_my_request", "denied")

    async def _cancel_after_send() -> None:
        for _ in range(50):
            await asyncio.sleep(0.01)
            async with reg._lock:  # type: ignore[attr-defined]
                if reg._entries:  # type: ignore[attr-defined]
                    cid = next(iter(reg._entries))  # type: ignore[attr-defined]
                    break
        else:
            raise AssertionError("no pending confirmation appeared")
        await reg.resolve(cid, telegram_user_id=42, allowed=False)

    results = await asyncio.gather(
        cb("mcp__cleanrr__remove_my_request", {"request_id": 7}, MagicMock()),
        _cancel_after_send(),
    )

    result = results[0]
    assert isinstance(result, PermissionResultDeny)
    assert _counter("remove_my_request", "denied") == before + 1


@pytest.mark.asyncio
async def test_write_tool_times_out_when_no_click() -> None:
    bot = _make_bot()
    reg = ConfirmationRegistry(ttl_seconds=0.1)
    settings = _settings(ttl=0.1)
    cb = make_can_use_tool(bot, reg, settings, formatters={}, telegram_user_id=42)

    before = _counter("remove_my_request", "timed_out")
    result = await cb("mcp__cleanrr__remove_my_request", {"request_id": 7}, MagicMock())

    assert isinstance(result, PermissionResultDeny)
    assert _counter("remove_my_request", "timed_out") == before + 1


@pytest.mark.asyncio
async def test_admin_only_tool_denies_non_admin_before_prompt() -> None:
    """ADMIN_ONLY_TOOLS must deny before any confirmation prompt is sent."""
    assert "delete_torrent" in ADMIN_ONLY_TOOLS
    bot = _make_bot()
    reg = ConfirmationRegistry(ttl_seconds=60)
    settings = _settings()  # no admin_telegram_ids

    before = cleanrr.metrics.tool_calls_total.labels(
        tool="delete_torrent", status="unauthorized"
    )._value.get()
    cb = make_can_use_tool(bot, reg, settings, formatters={}, telegram_user_id=42)

    result = await cb("mcp__cleanrr__delete_torrent", {"torrent_hash": "a" * 40}, MagicMock())

    assert isinstance(result, PermissionResultDeny)
    bot.send_message.assert_not_awaited()
    after = cleanrr.metrics.tool_calls_total.labels(
        tool="delete_torrent", status="unauthorized"
    )._value.get()
    assert after == before + 1


@pytest.mark.asyncio
async def test_admin_only_tool_reaches_prompt_for_admin() -> None:
    """An admin caller still goes through the normal confirmation flow."""
    bot = _make_bot()
    reg = ConfirmationRegistry(ttl_seconds=0.1)
    settings = _settings(ttl=0.1, admin_ids={42})
    cb = make_can_use_tool(bot, reg, settings, formatters={}, telegram_user_id=42)

    result = await cb("mcp__cleanrr__delete_torrent", {"torrent_hash": "a" * 40}, MagicMock())

    bot.send_message.assert_awaited_once()
    assert isinstance(result, PermissionResultDeny)  # timed out — no click in this test


@pytest.mark.asyncio
async def test_registry_full_denies_with_metric() -> None:
    bot = _make_bot()
    reg = ConfirmationRegistry(ttl_seconds=60)
    # Fill the registry across distinct users so the global cap fires for user 42.
    for i in range(100):
        cid = await reg.reserve(tool_name="remove_my_request", telegram_user_id=i)
        assert cid is not None
        await reg.register(
            confirmation_id=cid,
            telegram_user_id=i,
            tool_name="remove_my_request",
            tool_args={},
            prompt_message_id=1,
        )

    settings = _settings()
    cb = make_can_use_tool(bot, reg, settings, formatters={}, telegram_user_id=42)

    before = _counter("remove_my_request", "denied")
    result = await cb("mcp__cleanrr__remove_my_request", {"request_id": 7}, MagicMock())

    assert isinstance(result, PermissionResultDeny)
    assert _counter("remove_my_request", "denied") == before + 1
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_tools_set_is_explicit() -> None:
    assert "remove_my_request" in WRITE_TOOLS


# ---------------------------------------------------------------------------
# is_retired
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retired_denies_write_tool_without_reserving_or_sending() -> None:
    bot = _make_bot()
    reg = ConfirmationRegistry(ttl_seconds=60)
    settings = _settings()
    cb = make_can_use_tool(
        bot, reg, settings, formatters={}, telegram_user_id=42, is_retired=lambda: True
    )

    before_tool_calls = _tool_calls_counter("remove_my_request", "reset")
    before_destructive = _counter("remove_my_request", "denied")
    result = await cb("mcp__cleanrr__remove_my_request", {"request_id": 7}, MagicMock())

    assert isinstance(result, PermissionResultDeny)
    bot.send_message.assert_not_awaited()
    assert reg._entries == {}  # type: ignore[attr-defined]
    assert _tool_calls_counter("remove_my_request", "reset") == before_tool_calls + 1
    assert _counter("remove_my_request", "denied") == before_destructive


@pytest.mark.asyncio
async def test_reset_mid_send_cancels_the_prompt_once_registered() -> None:
    """/reset can mark the Agent retired after the pre-check but while
    send_message is still in flight — the prompt was never there for
    cancel_for_user to cancel until register() runs, so the fix must catch
    it there instead."""
    bot = _make_bot()
    retired_flag = [False]

    async def _send_message(**_kwargs: object) -> MagicMock:
        # Reset lands while this prompt is mid-send, before register() runs.
        retired_flag[0] = True
        sent = MagicMock()
        sent.message_id = 999
        return sent

    bot.send_message = AsyncMock(side_effect=_send_message)
    reg = ConfirmationRegistry(ttl_seconds=60)
    settings = _settings()
    cb = make_can_use_tool(
        bot,
        reg,
        settings,
        formatters={},
        telegram_user_id=42,
        is_retired=lambda: retired_flag[0],
    )

    before = _counter("remove_my_request", "denied")
    result = await cb("mcp__cleanrr__remove_my_request", {"request_id": 7}, MagicMock())

    assert isinstance(result, PermissionResultDeny)
    assert result.message == "the user reset this conversation"
    bot.edit_message_text.assert_awaited_once_with(chat_id=42, message_id=999, text="Cancelled.")
    assert _counter("remove_my_request", "denied") == before + 1
    assert await reg.has_pending_for_user(42) is False


@pytest.mark.asyncio
async def test_confirm_tap_after_reset_is_refused() -> None:
    """A Confirm tap that resolves the future True after /reset ran must
    still be refused — /reset only ever resolves a confirmation as denied,
    so anything that reads allowed=True here came from the button, not reset."""
    bot = _make_bot()
    reg = ConfirmationRegistry(ttl_seconds=60)
    settings = _settings()
    retired_flag = [False]
    cb = make_can_use_tool(
        bot,
        reg,
        settings,
        formatters={},
        telegram_user_id=42,
        is_retired=lambda: retired_flag[0],
    )

    async def _confirm_then_retire() -> None:
        for _ in range(50):
            await asyncio.sleep(0.01)
            async with reg._lock:  # type: ignore[attr-defined]
                if reg._entries:  # type: ignore[attr-defined]
                    cid = next(iter(reg._entries))  # type: ignore[attr-defined]
                    break
        else:
            raise AssertionError("no pending confirmation appeared")
        # /reset marks the Agent retired first, then the Confirm tap still
        # resolves the future True — can_use_tool must catch this after the
        # wait_for, not rely on the tap itself knowing about the reset.
        retired_flag[0] = True
        await reg.resolve(cid, telegram_user_id=42, allowed=True)

    before_tool_calls = _tool_calls_counter("remove_my_request", "reset")
    before_confirmed = _counter("remove_my_request", "confirmed")

    results = await asyncio.gather(
        cb("mcp__cleanrr__remove_my_request", {"request_id": 7}, MagicMock()),
        _confirm_then_retire(),
    )

    result = results[0]
    assert isinstance(result, PermissionResultDeny)
    assert result.message == "the user reset this conversation"
    assert _tool_calls_counter("remove_my_request", "reset") == before_tool_calls + 1
    assert _counter("remove_my_request", "confirmed") == before_confirmed + 1
    bot.edit_message_text.assert_awaited_once_with(chat_id=42, message_id=999, text="Cancelled.")


@pytest.mark.asyncio
async def test_retired_still_allows_a_read_only_tool() -> None:
    bot = _make_bot()
    reg = ConfirmationRegistry(ttl_seconds=60)
    settings = _settings()
    cb = make_can_use_tool(
        bot, reg, settings, formatters={}, telegram_user_id=42, is_retired=lambda: True
    )

    result = await cb("mcp__cleanrr__list_my_requests", {}, MagicMock())

    assert isinstance(result, PermissionResultAllow)
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_not_retired_sends_the_prompt_exactly_as_today() -> None:
    bot = _make_bot()
    reg = ConfirmationRegistry(ttl_seconds=60)
    settings = _settings()
    cb = make_can_use_tool(
        bot, reg, settings, formatters={}, telegram_user_id=42, is_retired=lambda: False
    )

    before = _counter("remove_my_request", "confirmed")

    async def _resolve_after_send() -> None:
        for _ in range(50):
            await asyncio.sleep(0.01)
            async with reg._lock:  # type: ignore[attr-defined]
                if reg._entries:  # type: ignore[attr-defined]
                    cid = next(iter(reg._entries))  # type: ignore[attr-defined]
                    break
        else:
            raise AssertionError("no pending confirmation appeared")
        await reg.resolve(cid, telegram_user_id=42, allowed=True)

    results = await asyncio.gather(
        cb("mcp__cleanrr__remove_my_request", {"request_id": 7}, MagicMock()),
        _resolve_after_send(),
    )

    result = results[0]
    assert isinstance(result, PermissionResultAllow)
    bot.send_message.assert_awaited_once()
    assert _counter("remove_my_request", "confirmed") == before + 1


# ---------------------------------------------------------------------------
# Confirmation prompt formatter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_my_request_formatter_enriches_with_title() -> None:
    client = AsyncMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "id": 7,
        "status": 1,
        "media": {"title": "Dune", "mediaType": "movie"},
    }
    client.get.return_value = resp

    formatters = build_confirmation_formatters(client, None, _settings())
    text = await formatters["remove_my_request"]({"request_id": 7})

    assert "Dune" in text
    assert "movie" in text
    assert "pending" in text


@pytest.mark.asyncio
async def test_remove_my_request_formatter_resolves_title_from_real_overseerr_shape() -> None:
    """Real Overseerr requests carry only tmdbId, never a title/name."""
    client = AsyncMock()
    request_resp = MagicMock()
    request_resp.status_code = 200
    request_resp.json.return_value = {
        "id": 7,
        "status": 1,
        "media": {"mediaType": "movie", "tmdbId": 194},
    }
    movie_detail_resp = MagicMock()
    movie_detail_resp.status_code = 200
    movie_detail_resp.json.return_value = {"id": 194, "title": "Amélie"}
    client.get.side_effect = [request_resp, movie_detail_resp]

    formatters = build_confirmation_formatters(client, None, _settings())
    text = await formatters["remove_my_request"]({"request_id": 7})

    assert "Amélie" in text


@pytest.mark.asyncio
async def test_remove_my_request_formatter_falls_back_on_http_error() -> None:
    import httpx as _httpx

    client = AsyncMock()
    client.get.side_effect = _httpx.RequestError("boom")

    formatters = build_confirmation_formatters(client, None, _settings())
    text = await formatters["remove_my_request"]({"request_id": 7})

    assert "#7" in text


@pytest.mark.asyncio
async def test_remove_my_request_formatter_falls_back_when_client_none() -> None:
    formatters = build_confirmation_formatters(None, None, _settings())
    text = await formatters["remove_my_request"]({"request_id": 7})
    assert "#7" in text


@pytest.mark.asyncio
async def test_remove_my_request_formatter_falls_back_on_non_200() -> None:
    client = AsyncMock()
    resp = MagicMock()
    resp.status_code = 404
    client.get.return_value = resp

    formatters = build_confirmation_formatters(client, None, _settings())
    text = await formatters["remove_my_request"]({"request_id": 7})

    assert "#7" in text


@pytest.mark.asyncio
async def test_remove_my_request_formatter_falls_back_on_malformed_json() -> None:
    client = AsyncMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("bad json")
    client.get.return_value = resp

    formatters = build_confirmation_formatters(client, None, _settings())
    text = await formatters["remove_my_request"]({"request_id": 7})

    assert "#7" in text


@pytest.mark.asyncio
async def test_remove_my_request_formatter_degrades_to_unknown_when_title_resolve_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow title lookup must degrade gracefully (still show status/media
    type, just no title) rather than losing the whole prompt to fallback."""
    import cleanrr.permissions._formatters as formatters_module

    monkeypatch.setattr(formatters_module, "_FORMATTER_TIMEOUT_SECONDS", 0.05)

    client = AsyncMock()
    request_resp = MagicMock()
    request_resp.status_code = 200
    request_resp.json.return_value = {
        "id": 7,
        "status": 1,
        "media": {"mediaType": "movie", "tmdbId": 194},
    }

    call_count = 0

    async def _get(*_args: object, **_kwargs: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return request_resp
        await asyncio.sleep(999)
        raise AssertionError("unreachable")

    client.get = _get

    formatters = build_confirmation_formatters(client, None, _settings())
    text = await asyncio.wait_for(formatters["remove_my_request"]({"request_id": 7}), timeout=5.0)

    # No resolved title available — falls back to "Unknown" rather than raising.
    assert "Unknown" in text


@pytest.mark.asyncio
async def test_remove_my_request_formatter_total_latency_bounded_not_stacked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the request fetch and title fetch used to each get their
    own full _FORMATTER_TIMEOUT_SECONDS budget, so a slow Overseerr could
    stack to ~2x the intended worst case. Both calls succeeding but each
    taking most of the budget must still finish within one shared deadline,
    not two sequential ones."""
    import cleanrr.permissions._formatters as formatters_module

    monkeypatch.setattr(formatters_module, "_FORMATTER_TIMEOUT_SECONDS", 0.2)

    client = AsyncMock()
    request_resp = MagicMock()
    request_resp.status_code = 200
    request_resp.json.return_value = {
        "id": 7,
        "status": 1,
        "media": {"mediaType": "movie", "tmdbId": 194},
    }
    title_resp = MagicMock()
    title_resp.status_code = 200
    title_resp.json.return_value = {"id": 194, "title": "Dune"}

    call_count = 0

    async def _get(*_args: object, **_kwargs: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        # Each individual call is well within the old (unbounded) per-call
        # budget, but two of them back-to-back would exceed one shared one.
        await asyncio.sleep(0.15)
        return request_resp if call_count == 1 else title_resp

    client.get = _get

    formatters = build_confirmation_formatters(client, None, _settings())
    start = time.monotonic()
    text = await formatters["remove_my_request"]({"request_id": 7})
    elapsed = time.monotonic() - start

    # Both calls were slow enough to blow the shared budget, so the second
    # one must have been cut short — falls back rather than doubling latency.
    assert elapsed < 0.35
    assert "Dune" not in text


@pytest.mark.asyncio
async def test_remove_my_request_formatter_caps_overlong_title() -> None:
    client = AsyncMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "id": 7,
        "status": 1,
        "media": {"title": "X" * 500, "mediaType": "movie"},
    }
    client.get.return_value = resp

    formatters = build_confirmation_formatters(client, None, _settings())
    text = await formatters["remove_my_request"]({"request_id": 7})

    # Confirms the 80-char cap is in place; allow some room for prefix/suffix.
    assert text.count("X") <= 80


def test_request_status_label_known_and_unknown() -> None:
    assert _request_status_label(1) == "pending"
    assert _request_status_label(2) == "approved"
    assert _request_status_label(3) == "declined"
    assert _request_status_label(4) == "failed"
    assert _request_status_label(5) == "completed"
    assert _request_status_label(99) == "status 99"
    assert _request_status_label(None) == "unknown"
    assert _request_status_label("foo") == "unknown"


def test_format_bytes_invalid_and_negative() -> None:
    assert _format_bytes("not a number") == "?"
    assert _format_bytes(-1) == "?"


def test_format_bytes_mb_range() -> None:
    assert _format_bytes(5_242_880) == "5 MB"


def test_format_bytes_byte_range() -> None:
    assert _format_bytes(512) == "512 B"


@pytest.mark.asyncio
async def test_delete_torrent_formatter_falls_back_when_client_none() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="t",  # type: ignore[arg-type]
        anthropic_api_key="sk",  # type: ignore[arg-type]
    )
    formatters = build_confirmation_formatters(None, None, settings)
    text = await formatters["delete_torrent"]({"torrent_hash": "a" * 40})
    assert "a" * 40 in text


@pytest.mark.asyncio
async def test_delete_torrent_formatter_falls_back_on_non_200() -> None:
    qbit = AsyncMock()
    resp = MagicMock()
    resp.status_code = 500
    qbit.get.return_value = resp
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="t",  # type: ignore[arg-type]
        anthropic_api_key="sk",  # type: ignore[arg-type]
        qbittorrent_url="http://qbit:8080",  # type: ignore[arg-type]
        qbittorrent_username="admin",
        qbittorrent_password="x",  # type: ignore[arg-type]
    )

    formatters = build_confirmation_formatters(None, qbit, settings)
    text = await formatters["delete_torrent"]({"torrent_hash": "a" * 40})
    assert "a" * 40 in text


@pytest.mark.asyncio
async def test_delete_torrent_formatter_falls_back_on_malformed_json() -> None:
    qbit = AsyncMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("bad json")
    qbit.get.return_value = resp
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="t",  # type: ignore[arg-type]
        anthropic_api_key="sk",  # type: ignore[arg-type]
        qbittorrent_url="http://qbit:8080",  # type: ignore[arg-type]
        qbittorrent_username="admin",
        qbittorrent_password="x",  # type: ignore[arg-type]
    )

    formatters = build_confirmation_formatters(None, qbit, settings)
    text = await formatters["delete_torrent"]({"torrent_hash": "a" * 40})
    assert "a" * 40 in text


@pytest.mark.asyncio
async def test_delete_torrent_formatter_falls_back_on_non_dict_entry() -> None:
    qbit = AsyncMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = ["not-a-dict"]
    qbit.get.return_value = resp
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="t",  # type: ignore[arg-type]
        anthropic_api_key="sk",  # type: ignore[arg-type]
        qbittorrent_url="http://qbit:8080",  # type: ignore[arg-type]
        qbittorrent_username="admin",
        qbittorrent_password="x",  # type: ignore[arg-type]
    )

    formatters = build_confirmation_formatters(None, qbit, settings)
    text = await formatters["delete_torrent"]({"torrent_hash": "a" * 40})
    assert "a" * 40 in text


@pytest.mark.asyncio
async def test_delete_torrent_formatter_enriches_with_name_and_size() -> None:
    qbit = AsyncMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = [{"name": "Big.Movie", "size": 5_368_709_120}]
    qbit.get.return_value = resp
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="t",  # type: ignore[arg-type]
        anthropic_api_key="sk",  # type: ignore[arg-type]
        qbittorrent_url="http://qbit:8080",  # type: ignore[arg-type]
        qbittorrent_username="admin",
        qbittorrent_password="x",  # type: ignore[arg-type]
    )

    formatters = build_confirmation_formatters(None, qbit, settings)
    text = await formatters["delete_torrent"]({"torrent_hash": "a" * 40})

    assert "Big.Movie" in text
    assert "GB" in text
    assert "cannot be undone" in text.lower()


@pytest.mark.asyncio
async def test_delete_torrent_formatter_falls_back_on_unknown_hash() -> None:
    qbit = AsyncMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = []
    qbit.get.return_value = resp
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="t",  # type: ignore[arg-type]
        anthropic_api_key="sk",  # type: ignore[arg-type]
        qbittorrent_url="http://qbit:8080",  # type: ignore[arg-type]
        qbittorrent_username="admin",
        qbittorrent_password="x",  # type: ignore[arg-type]
    )

    formatters = build_confirmation_formatters(None, qbit, settings)
    text = await formatters["delete_torrent"]({"torrent_hash": "a" * 40})

    assert "a" * 40 in text  # fallback uses the hash directly


@pytest.mark.asyncio
async def test_delete_torrent_formatter_strips_whitespace_like_the_tool() -> None:
    """Formatter and tool must agree on what counts as a valid hash, or the prompt lies."""
    qbit = AsyncMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = [{"name": "Movie.X", "size": 1_073_741_824}]
    qbit.get.return_value = resp
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="t",  # type: ignore[arg-type]
        anthropic_api_key="sk",  # type: ignore[arg-type]
        qbittorrent_url="http://qbit:8080",  # type: ignore[arg-type]
        qbittorrent_username="admin",
        qbittorrent_password="x",  # type: ignore[arg-type]
    )

    formatters = build_confirmation_formatters(None, qbit, settings)
    text = await formatters["delete_torrent"]({"torrent_hash": "  " + "a" * 40 + "  "})

    # The tool would accept after stripping; the formatter must too, so the
    # prompt and the action stay in sync.
    assert "invalid hash" not in text.lower()
    assert "Movie.X" in text


@pytest.mark.asyncio
async def test_delete_torrent_formatter_rejects_invalid_hash_without_http_call() -> None:
    qbit = AsyncMock()
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="t",  # type: ignore[arg-type]
        anthropic_api_key="sk",  # type: ignore[arg-type]
        qbittorrent_url="http://qbit:8080",  # type: ignore[arg-type]
        qbittorrent_username="admin",
        qbittorrent_password="x",  # type: ignore[arg-type]
    )

    formatters = build_confirmation_formatters(None, qbit, settings)

    for bad in ["", "abc", "g" * 40, "X" * 5000, None, 42]:
        text = await formatters["delete_torrent"]({"torrent_hash": bad})
        # All bad inputs flag invalid + bounded length
        assert "invalid hash" in text.lower()
        assert len(text) < 200
    qbit.get.assert_not_called()


@pytest.mark.asyncio
async def test_delete_torrent_formatter_shows_missing_for_absent_hash() -> None:
    """None, empty, whitespace-only, non-str and zero-width-space hashes all
    bound to "" via bound_text, so the prompt falls back to <missing> instead
    of an empty "invalid hash: ..." suffix. The "\u200b" case is the one
    output change this branch introduced: the old `raw.strip()` guard kept a
    zero-width space because `.strip()` removes only whitespace."""
    qbit = AsyncMock()
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="t",  # type: ignore[arg-type]
        anthropic_api_key="sk",  # type: ignore[arg-type]
        qbittorrent_url="http://qbit:8080",  # type: ignore[arg-type]
        qbittorrent_username="admin",
        qbittorrent_password="x",  # type: ignore[arg-type]
    )

    formatters = build_confirmation_formatters(None, qbit, settings)

    for bad in [None, "", "   ", 42, "\u200b"]:
        text = await formatters["delete_torrent"]({"torrent_hash": bad})
        assert "(invalid hash: <missing>)" in text
    qbit.get.assert_not_called()


@pytest.mark.asyncio
async def test_delete_torrent_formatter_falls_back_on_http_error() -> None:
    qbit = AsyncMock()
    qbit.get.side_effect = httpx.RequestError("boom")
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="t",  # type: ignore[arg-type]
        anthropic_api_key="sk",  # type: ignore[arg-type]
        qbittorrent_url="http://qbit:8080",  # type: ignore[arg-type]
        qbittorrent_username="admin",
        qbittorrent_password="x",  # type: ignore[arg-type]
    )

    formatters = build_confirmation_formatters(None, qbit, settings)
    text = await formatters["delete_torrent"]({"torrent_hash": "a" * 40})
    assert "a" * 40 in text


@pytest.mark.asyncio
async def test_force_research_movie_formatter_uses_title() -> None:
    formatters = build_confirmation_formatters(None, None, _settings())
    text = await formatters["force_research_movie"]({"title": "Dune"})
    assert "Dune" in text
    assert "Radarr" in text


@pytest.mark.asyncio
async def test_force_research_show_formatter_uses_title() -> None:
    formatters = build_confirmation_formatters(None, None, _settings())
    text = await formatters["force_research_show"]({"title": "The Bear"})
    assert "The Bear" in text
    assert "Sonarr" in text
    assert "series" in text.lower()


@pytest.mark.asyncio
async def test_force_research_formatters_warn_title_may_not_match() -> None:
    """Regression: the prompt can't cheaply verify the title against
    Overseerr before confirmation (that's a full fuzzy-match pass, not a
    lookup-by-id) — must set honest expectations rather than imply success,
    since a not-actually-requested title still shows "Confirmed." before
    the tool's own validation reports no match."""
    formatters = build_confirmation_formatters(None, None, _settings())
    movie_text = await formatters["force_research_movie"]({"title": "The Flash"})
    show_text = await formatters["force_research_show"]({"title": "The Flash"})
    assert "not one of your requests" in movie_text
    assert "not one of your requests" in show_text


@pytest.mark.asyncio
async def test_force_research_formatters_handle_empty_title() -> None:
    formatters = build_confirmation_formatters(None, None, _settings())
    movie_text = await formatters["force_research_movie"]({"title": ""})
    show_text = await formatters["force_research_show"]({})
    # Don't crash on missing/empty title — fall back to placeholder
    assert movie_text
    assert show_text


@pytest.mark.asyncio
async def test_remove_my_request_formatter_sanitises_title_and_media_type() -> None:
    """Red if line 68 or line 69 reverts to its str(...)[:N] slice: the
    zero-width space, the newline and the bell all survive that slice."""
    client = AsyncMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "id": 7,
        "status": 1,
        "media": {"title": "Du\u200bne\nPart Two", "mediaType": "movie\x07"},
    }
    client.get.return_value = resp

    formatters = build_confirmation_formatters(client, None, _settings())
    text = await formatters["remove_my_request"]({"request_id": 7})

    assert "Cancel request: Du ne Part Two (movie, status: pending)?" in text
    assert "\u200b" not in text
    assert "\n" not in text
    assert "\x07" not in text


@pytest.mark.asyncio
async def test_remove_my_request_formatter_caps_media_type() -> None:
    """Passes against the old code by design — its regressing edit is
    dropping or widening limit=20, the limit this change must preserve."""
    client = AsyncMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "id": 7,
        "status": 1,
        "media": {"title": "Dune", "mediaType": "m" * 40},
    }
    client.get.return_value = resp

    formatters = build_confirmation_formatters(client, None, _settings())
    text = await formatters["remove_my_request"]({"request_id": 7})

    assert "m" * 20 in text
    assert "m" * 21 not in text


@pytest.mark.asyncio
async def test_delete_torrent_formatter_sanitises_invalid_hash_echo() -> None:
    """Red if line 118 reverts to raw.strip()[:40] + '...'."""
    qbit = AsyncMock()
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="t",  # type: ignore[arg-type]
        anthropic_api_key="sk",  # type: ignore[arg-type]
        qbittorrent_url="http://qbit:8080",  # type: ignore[arg-type]
        qbittorrent_username="admin",
        qbittorrent_password="x",  # type: ignore[arg-type]
    )

    formatters = build_confirmation_formatters(None, qbit, settings)
    text = await formatters["delete_torrent"]({"torrent_hash": "  ab\u200bcd\nef  "})

    assert "invalid hash: ab cd ef..." in text
    assert "\u200b" not in text
    assert "\n" not in text
    # Proves the ellipsis and the sentence survived the migration.
    assert text.endswith("AND its files? Tool will refuse.")
    qbit.get.assert_not_called()


@pytest.mark.asyncio
async def test_delete_torrent_formatter_caps_invalid_hash_echo() -> None:
    """Limit-preservation guard — regressing edit is dropping limit=40."""
    qbit = AsyncMock()
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="t",  # type: ignore[arg-type]
        anthropic_api_key="sk",  # type: ignore[arg-type]
        qbittorrent_url="http://qbit:8080",  # type: ignore[arg-type]
        qbittorrent_username="admin",
        qbittorrent_password="x",  # type: ignore[arg-type]
    )

    formatters = build_confirmation_formatters(None, qbit, settings)
    text = await formatters["delete_torrent"]({"torrent_hash": "z" * 100})

    assert "z" * 40 + "..." in text
    assert "z" * 41 not in text


@pytest.mark.asyncio
async def test_delete_torrent_formatter_sanitises_torrent_name() -> None:
    """Red if line 146 reverts to str(entry.get("name") or "unknown")[:80]."""
    qbit = AsyncMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = [{"name": "Big\u200bMovie\r\nS01", "size": 1_073_741_824}]
    qbit.get.return_value = resp
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="t",  # type: ignore[arg-type]
        anthropic_api_key="sk",  # type: ignore[arg-type]
        qbittorrent_url="http://qbit:8080",  # type: ignore[arg-type]
        qbittorrent_username="admin",
        qbittorrent_password="x",  # type: ignore[arg-type]
    )

    formatters = build_confirmation_formatters(None, qbit, settings)
    text = await formatters["delete_torrent"]({"torrent_hash": "a" * 40})

    assert "Delete torrent 'Big Movie S01' (1.0 GB)" in text
    assert "\u200b" not in text
    assert "\r" not in text
    assert "\n" not in text


@pytest.mark.asyncio
async def test_delete_torrent_formatter_caps_torrent_name() -> None:
    """Limit-preservation guard for the torrent-name site."""
    qbit = AsyncMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = [{"name": "N" * 200, "size": 1_073_741_824}]
    qbit.get.return_value = resp
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="t",  # type: ignore[arg-type]
        anthropic_api_key="sk",  # type: ignore[arg-type]
        qbittorrent_url="http://qbit:8080",  # type: ignore[arg-type]
        qbittorrent_username="admin",
        qbittorrent_password="x",  # type: ignore[arg-type]
    )

    formatters = build_confirmation_formatters(None, qbit, settings)
    text = await formatters["delete_torrent"]({"torrent_hash": "a" * 40})

    assert "N" * 80 in text
    assert "N" * 81 not in text


@pytest.mark.asyncio
async def test_force_research_formatters_sanitise_title() -> None:
    """Red if line 158 or line 174 reverts to its str(...)[:80] slice."""
    formatters = build_confirmation_formatters(None, None, _settings())
    movie_text = await formatters["force_research_movie"]({"title": "Du\u200bne\nPart Two"})
    show_text = await formatters["force_research_show"]({"title": "The\u200bBear\r\nS03"})

    assert "Re-search Radarr for 'Du ne Part Two'?" in movie_text
    assert "Re-search Sonarr for 'The Bear S03' (whole series)?" in show_text
    for text in (movie_text, show_text):
        assert "\u200b" not in text
        assert "\r" not in text
        assert "\n" not in text


@pytest.mark.asyncio
async def test_force_research_formatters_fall_back_for_a_non_string_title() -> None:
    """The one deliberate output change in this plan: a non-str or
    all-non-printable title now renders as the placeholder instead of
    str(value) (e.g. '42') or a bare invisible string."""
    formatters = build_confirmation_formatters(None, None, _settings())

    movie_text = await formatters["force_research_movie"]({"title": 42})
    assert "your movie" in movie_text

    show_text = await formatters["force_research_show"]({"title": ["a"]})
    assert "your show" in show_text

    zero_width_text = await formatters["force_research_movie"]({"title": "\u200b"})
    assert "your movie" in zero_width_text
    assert "\u200b" not in zero_width_text


def test_write_tools_set_includes_all_destructive_tools() -> None:
    expected = {
        "remove_my_request",
        "delete_torrent",
        "force_research_movie",
        "force_research_show",
    }
    assert expected.issubset(WRITE_TOOLS)


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def test_telegram_bot_param_is_not_required_to_make_callback() -> None:
    """A None bot is allowed for testing — callback won't be invoked in that case."""
    settings = _settings()
    bot: Any = MagicMock()
    reg = ConfirmationRegistry(ttl_seconds=60)
    cb = make_can_use_tool(bot, reg, settings, formatters={}, telegram_user_id=42)
    assert callable(cb)


@pytest.mark.asyncio
async def test_can_use_tool_denies_when_send_message_fails() -> None:
    from telegram.error import TelegramError as _TelegramError

    bot = _make_bot()
    bot.send_message = AsyncMock(side_effect=_TelegramError("network"))
    reg = ConfirmationRegistry(ttl_seconds=60)
    settings = _settings()
    cb = make_can_use_tool(bot, reg, settings, formatters={}, telegram_user_id=42)

    before = _counter("remove_my_request", "denied")
    result = await cb("mcp__cleanrr__remove_my_request", {"request_id": 7}, MagicMock())

    assert isinstance(result, PermissionResultDeny)
    assert _counter("remove_my_request", "denied") == before + 1


@pytest.mark.asyncio
async def test_can_use_tool_falls_back_when_formatter_crashes() -> None:
    """A buggy formatter must not break the confirmation flow."""
    bot = _make_bot()
    reg = ConfirmationRegistry(ttl_seconds=0.1)
    settings = _settings(ttl=0.1)

    async def bad_formatter(_tool_args: dict[str, Any]) -> str:
        raise ValueError("boom")

    cb = make_can_use_tool(
        bot, reg, settings, formatters={"remove_my_request": bad_formatter}, telegram_user_id=42
    )

    await cb("mcp__cleanrr__remove_my_request", {"request_id": 7}, MagicMock())

    bot.send_message.assert_awaited_once()
    assert bot.send_message.call_args.kwargs["text"] == "Run remove_my_request?"


@pytest.mark.asyncio
async def test_sweeper_actively_evicts_expired_entries() -> None:
    """The background sweep loop must evict entries without anyone calling get().

    Sweep interval has a 1.0s floor regardless of TTL, so the test must wait
    out at least one full interval after expiry.
    """
    reg = ConfirmationRegistry(ttl_seconds=0.05)
    await reg.start()
    try:
        cid = await reg.reserve(tool_name="remove_my_request", telegram_user_id=1)
        assert cid is not None
        pending = await reg.register(
            confirmation_id=cid,
            telegram_user_id=1,
            tool_name="remove_my_request",
            tool_args={},
            prompt_message_id=1,
        )
        await asyncio.sleep(1.3)
        assert pending.future.done()
        assert pending.outcome == "timed_out"
    finally:
        await reg.stop()


@pytest.mark.asyncio
async def test_edit_outcome_failure_is_swallowed() -> None:
    """If editing the confirmation message fails, can_use_tool still returns cleanly."""
    from telegram.error import TelegramError as _TelegramError

    bot = _make_bot()
    bot.edit_message_text = AsyncMock(side_effect=_TelegramError("can't edit"))
    reg = ConfirmationRegistry(ttl_seconds=60)
    settings = _settings()
    cb = make_can_use_tool(bot, reg, settings, formatters={}, telegram_user_id=42)

    async def _confirm_after_send() -> None:
        for _ in range(50):
            await asyncio.sleep(0.01)
            async with reg._lock:  # type: ignore[attr-defined]
                if reg._entries:  # type: ignore[attr-defined]
                    cid = next(iter(reg._entries))  # type: ignore[attr-defined]
                    break
        else:
            raise AssertionError("no pending appeared")
        await reg.resolve(cid, telegram_user_id=42, allowed=True)

    results = await asyncio.gather(
        cb("mcp__cleanrr__remove_my_request", {"request_id": 7}, MagicMock()),
        _confirm_after_send(),
    )

    assert isinstance(results[0], PermissionResultAllow)


@pytest.mark.asyncio
async def test_resolve_returns_false_when_id_unknown() -> None:
    reg = ConfirmationRegistry(ttl_seconds=60)
    ok = await reg.resolve("nonexistent", telegram_user_id=1, allowed=True)
    assert ok is False


@pytest.mark.asyncio
async def test_resolve_returns_false_when_future_already_done() -> None:
    reg = ConfirmationRegistry(ttl_seconds=60)
    cid = await reg.reserve(tool_name="remove_my_request", telegram_user_id=42)
    assert cid is not None
    pending = await reg.register(
        confirmation_id=cid,
        telegram_user_id=42,
        tool_name="remove_my_request",
        tool_args={},
        prompt_message_id=1,
    )
    # Resolve once via direct future-set (simulating a race).
    pending.future.set_result(True)
    ok = await reg.resolve(cid, telegram_user_id=42, allowed=False)
    assert ok is False


@pytest.mark.asyncio
async def test_timeout_on_unknown_id_is_noop() -> None:
    reg = ConfirmationRegistry(ttl_seconds=60)
    # Should not raise
    await reg.timeout("nonexistent")


def test_ttl_seconds_property_returns_configured_value() -> None:
    reg = ConfirmationRegistry(ttl_seconds=42.5)
    assert reg.ttl_seconds == 42.5
