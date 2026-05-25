from __future__ import annotations

import asyncio
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
    _request_status_label,
    build_confirmation_formatters,
    make_can_use_tool,
)
from cleanrr.tools._context import current_telegram_user_id


def _settings(ttl: float = 60.0) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="t",  # type: ignore[arg-type]
        anthropic_api_key="sk",  # type: ignore[arg-type]
        confirmation_ttl_seconds=ttl,
        overseerr_url="http://overseerr:5055",  # type: ignore[arg-type]
        overseerr_api_key="key",  # type: ignore[arg-type]
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


# ---------------------------------------------------------------------------
# make_can_use_tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_tool_allows_immediately_without_telegram_message() -> None:
    bot = _make_bot()
    reg = ConfirmationRegistry(ttl_seconds=60)
    settings = _settings()
    cb = make_can_use_tool(bot, reg, settings, formatters={})

    result = await cb("mcp__cleanrr__list_my_requests", {"foo": "bar"}, MagicMock())

    assert isinstance(result, PermissionResultAllow)
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_tool_with_confirm_returns_allow_and_increments_metric() -> None:
    bot = _make_bot()
    reg = ConfirmationRegistry(ttl_seconds=60)
    settings = _settings()
    cb = make_can_use_tool(bot, reg, settings, formatters={})

    before = _counter("remove_my_request", "confirmed")
    token = current_telegram_user_id.set(42)

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

    try:
        results = await asyncio.gather(
            cb("mcp__cleanrr__remove_my_request", {"request_id": 7}, MagicMock()),
            _resolve_after_send(),
        )
    finally:
        current_telegram_user_id.reset(token)

    result = results[0]
    assert isinstance(result, PermissionResultAllow)
    bot.send_message.assert_awaited_once()
    assert _counter("remove_my_request", "confirmed") == before + 1


@pytest.mark.asyncio
async def test_write_tool_with_cancel_returns_deny_and_increments_metric() -> None:
    bot = _make_bot()
    reg = ConfirmationRegistry(ttl_seconds=60)
    settings = _settings()
    cb = make_can_use_tool(bot, reg, settings, formatters={})

    before = _counter("remove_my_request", "denied")
    token = current_telegram_user_id.set(42)

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

    try:
        results = await asyncio.gather(
            cb("mcp__cleanrr__remove_my_request", {"request_id": 7}, MagicMock()),
            _cancel_after_send(),
        )
    finally:
        current_telegram_user_id.reset(token)

    result = results[0]
    assert isinstance(result, PermissionResultDeny)
    assert _counter("remove_my_request", "denied") == before + 1


@pytest.mark.asyncio
async def test_write_tool_times_out_when_no_click() -> None:
    bot = _make_bot()
    reg = ConfirmationRegistry(ttl_seconds=0.1)
    settings = _settings(ttl=0.1)
    cb = make_can_use_tool(bot, reg, settings, formatters={})

    before = _counter("remove_my_request", "timed_out")
    token = current_telegram_user_id.set(42)
    try:
        result = await cb("mcp__cleanrr__remove_my_request", {"request_id": 7}, MagicMock())
    finally:
        current_telegram_user_id.reset(token)

    assert isinstance(result, PermissionResultDeny)
    assert _counter("remove_my_request", "timed_out") == before + 1


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
    cb = make_can_use_tool(bot, reg, settings, formatters={})

    before = _counter("remove_my_request", "denied")
    token = current_telegram_user_id.set(42)
    try:
        result = await cb("mcp__cleanrr__remove_my_request", {"request_id": 7}, MagicMock())
    finally:
        current_telegram_user_id.reset(token)

    assert isinstance(result, PermissionResultDeny)
    assert _counter("remove_my_request", "denied") == before + 1
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_tools_set_is_explicit() -> None:
    assert "remove_my_request" in WRITE_TOOLS


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
async def test_remove_my_request_formatter_falls_back_on_http_error() -> None:
    import httpx as _httpx

    client = AsyncMock()
    client.get.side_effect = _httpx.RequestError("boom")

    formatters = build_confirmation_formatters(client, None, _settings())
    text = await formatters["remove_my_request"]({"request_id": 7})

    assert "#7" in text


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
    assert _request_status_label(99) == "status 99"
    assert _request_status_label(None) == "unknown"
    assert _request_status_label("foo") == "unknown"


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
async def test_force_research_formatters_handle_empty_title() -> None:
    formatters = build_confirmation_formatters(None, None, _settings())
    movie_text = await formatters["force_research_movie"]({"title": ""})
    show_text = await formatters["force_research_show"]({})
    # Don't crash on missing/empty title — fall back to placeholder
    assert movie_text
    assert show_text


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
    cb = make_can_use_tool(bot, reg, settings, formatters={})
    assert callable(cb)


@pytest.mark.asyncio
async def test_can_use_tool_denies_when_contextvar_not_set() -> None:
    """can_use_tool must deny gracefully if the per-request contextvar wasn't set."""
    bot = _make_bot()
    reg = ConfirmationRegistry(ttl_seconds=60)
    settings = _settings()
    cb = make_can_use_tool(bot, reg, settings, formatters={})

    # Note: NOT setting current_telegram_user_id
    result = await cb("mcp__cleanrr__remove_my_request", {"request_id": 7}, MagicMock())

    assert isinstance(result, PermissionResultDeny)
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_can_use_tool_denies_when_send_message_fails() -> None:
    from telegram.error import TelegramError as _TelegramError

    bot = _make_bot()
    bot.send_message = AsyncMock(side_effect=_TelegramError("network"))
    reg = ConfirmationRegistry(ttl_seconds=60)
    settings = _settings()
    cb = make_can_use_tool(bot, reg, settings, formatters={})

    before = _counter("remove_my_request", "denied")
    token = current_telegram_user_id.set(42)
    try:
        result = await cb("mcp__cleanrr__remove_my_request", {"request_id": 7}, MagicMock())
    finally:
        current_telegram_user_id.reset(token)

    assert isinstance(result, PermissionResultDeny)
    assert _counter("remove_my_request", "denied") == before + 1


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
    cb = make_can_use_tool(bot, reg, settings, formatters={})

    token = current_telegram_user_id.set(42)

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

    try:
        results = await asyncio.gather(
            cb("mcp__cleanrr__remove_my_request", {"request_id": 7}, MagicMock()),
            _confirm_after_send(),
        )
    finally:
        current_telegram_user_id.reset(token)

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
