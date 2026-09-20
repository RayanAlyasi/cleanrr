from __future__ import annotations

import asyncio
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from cleanrr import metrics
from cleanrr.agent import Agent
from cleanrr.agent_pool import _POOL_MAX_AGENTS, AgentPool
from cleanrr.config import Settings
from cleanrr.identity import Identity


def _settings(*, agent_idle_timeout_minutes: int = 30) -> Settings:
    return Settings(
        telegram_bot_token=SecretStr("test"),
        anthropic_api_key=SecretStr("sk-test"),
        agent_idle_timeout_minutes=agent_idle_timeout_minutes,
    )


def _make_pool(**overrides: object) -> AgentPool:
    kwargs: dict[str, object] = {
        "identity": MagicMock(spec=Identity),
        "settings": _settings(),
        "timeout_seconds": 5.0,
    }
    kwargs.update(overrides)
    return AgentPool(**kwargs)  # type: ignore[arg-type]


def _make_pool_with_idle_timeout(agent_idle_timeout_minutes: int, **overrides: object) -> AgentPool:
    return _make_pool(
        settings=_settings(agent_idle_timeout_minutes=agent_idle_timeout_minutes),
        **overrides,
    )


def _mock_agent(agent: Agent | None) -> MagicMock:
    """Narrow a pool-returned Agent to the MagicMock _fake_agent_class built,
    so tests can set mock-only attributes (idle_seconds, side_effect, ...)
    without pyright flagging assignment to Agent's own read-only properties."""
    assert agent is not None
    return cast(MagicMock, agent)


def _fake_agent_class() -> MagicMock:
    """Stand-in for cleanrr.agent.Agent — each call returns a fresh mock
    instance with AsyncMock start/stop/retire and the idle-tracking surface
    AgentPool relies on, so tests exercise AgentPool's own logic without
    spawning a real subprocess."""
    created: list[MagicMock] = []

    def _construct(**_kwargs: object) -> MagicMock:
        instance = MagicMock()
        instance.start = AsyncMock()
        instance.stop = AsyncMock()
        instance.retire = AsyncMock()
        instance.touch = MagicMock()
        instance.mark_retired = MagicMock()
        instance.is_busy = False
        instance.is_retired = False
        instance.idle_seconds = 0.0
        created.append(instance)
        return instance

    factory = MagicMock(side_effect=_construct)
    factory.created = created  # type: ignore[attr-defined]
    return factory


@pytest.mark.asyncio
async def test_get_or_create_starts_a_new_agent_for_new_user() -> None:
    pool = _make_pool()
    fake_agent_cls = _fake_agent_class()

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        agent = await pool.get_or_create(1)

    assert agent is not None
    agent.start.assert_awaited_once_with(1)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_get_or_create_returns_cached_agent_for_same_user() -> None:
    pool = _make_pool()
    fake_agent_cls = _fake_agent_class()

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        first = await pool.get_or_create(1)
        second = await pool.get_or_create(1)

    assert first is second
    fake_agent_cls.assert_called_once()


@pytest.mark.asyncio
async def test_get_or_create_creates_distinct_agents_per_user() -> None:
    pool = _make_pool()
    fake_agent_cls = _fake_agent_class()

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        agent_1 = await pool.get_or_create(1)
        agent_2 = await pool.get_or_create(2)

    assert agent_1 is not agent_2
    assert fake_agent_cls.call_count == 2


@pytest.mark.asyncio
async def test_get_or_create_refuses_new_user_past_capacity() -> None:
    pool = _make_pool()
    fake_agent_cls = _fake_agent_class()

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        for user_id in range(_POOL_MAX_AGENTS):
            agent = await pool.get_or_create(user_id)
            assert agent is not None

        overflow = await pool.get_or_create(9999)

    assert overflow is None
    assert fake_agent_cls.call_count == _POOL_MAX_AGENTS


@pytest.mark.asyncio
async def test_get_or_create_still_serves_existing_user_at_capacity() -> None:
    """A friendly at-capacity refusal must only apply to brand-new users —
    someone already in the pool keeps working even while it's full."""
    pool = _make_pool()
    fake_agent_cls = _fake_agent_class()

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        for user_id in range(_POOL_MAX_AGENTS):
            await pool.get_or_create(user_id)

        again = await pool.get_or_create(0)

    assert again is fake_agent_cls.created[0]
    assert fake_agent_cls.call_count == _POOL_MAX_AGENTS


@pytest.mark.asyncio
async def test_get_or_create_forwards_shared_resources_to_agent() -> None:
    identity = MagicMock(spec=Identity)
    settings = _settings()
    telegram_bot = MagicMock()
    overseerr_client = MagicMock()
    confirmation_registry = MagicMock()
    pool = _make_pool(
        identity=identity,
        settings=settings,
        model="opus",
        system_prompt="custom prompt",
        timeout_seconds=7.5,
        telegram_bot=telegram_bot,
        overseerr_client=overseerr_client,
        confirmation_registry=confirmation_registry,
    )
    fake_agent_cls = _fake_agent_class()

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        await pool.get_or_create(1)

    fake_agent_cls.assert_called_once_with(
        identity=identity,
        settings=settings,
        model="opus",
        system_prompt="custom prompt",
        timeout_seconds=7.5,
        telegram_bot=telegram_bot,
        overseerr_client=overseerr_client,
        sonarr_client=None,
        radarr_client=None,
        qbit_client=None,
        confirmation_registry=confirmation_registry,
    )


@pytest.mark.asyncio
async def test_get_or_create_touches_new_and_cached_agent() -> None:
    pool = _make_pool()
    fake_agent_cls = _fake_agent_class()

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        first = _mock_agent(await pool.get_or_create(1))
        second = _mock_agent(await pool.get_or_create(1))

    assert first is second
    assert first.touch.call_count == 2


@pytest.mark.asyncio
async def test_agent_pool_agents_gauge_tracks_pool_size() -> None:
    pool = _make_pool()
    fake_agent_cls = _fake_agent_class()

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        await pool.get_or_create(1)
        await pool.get_or_create(2)
        assert metrics.agent_pool_agents._value.get() == 2  # type: ignore[attr-defined]

        await pool.reset(1)
        assert metrics.agent_pool_agents._value.get() == 1  # type: ignore[attr-defined]

        await pool.stop()
        assert metrics.agent_pool_agents._value.get() == 0  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_sweep_once_retires_idle_agent() -> None:
    pool = _make_pool()
    fake_agent_cls = _fake_agent_class()

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        agent = _mock_agent(await pool.get_or_create(1))
    agent.idle_seconds = 9999.0

    before = metrics.agent_evictions_total.labels(reason="idle")._value.get()  # type: ignore[attr-defined]
    await pool._sweep_once()
    await asyncio.gather(*pool._retiring)

    agent.mark_retired.assert_called_once()
    agent.retire.assert_awaited_once()
    assert 1 not in pool._agents
    after = metrics.agent_evictions_total.labels(reason="idle")._value.get()  # type: ignore[attr-defined]
    assert after == before + 1


@pytest.mark.asyncio
async def test_sweep_once_leaves_busy_agent_alone() -> None:
    pool = _make_pool()
    fake_agent_cls = _fake_agent_class()

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        agent = _mock_agent(await pool.get_or_create(1))
    agent.is_busy = True
    agent.idle_seconds = 9999.0

    before = metrics.agent_evictions_total.labels(reason="idle")._value.get()  # type: ignore[attr-defined]
    await pool._sweep_once()

    assert 1 in pool._agents
    agent.retire.assert_not_awaited()
    after = metrics.agent_evictions_total.labels(reason="idle")._value.get()  # type: ignore[attr-defined]
    assert after == before


@pytest.mark.asyncio
async def test_sweep_once_leaves_agent_with_pending_confirmation_alone() -> None:
    registry = MagicMock()
    registry.has_pending_for_user = AsyncMock(return_value=True)
    pool = _make_pool(confirmation_registry=registry)
    fake_agent_cls = _fake_agent_class()

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        agent = _mock_agent(await pool.get_or_create(1))
    agent.idle_seconds = 9999.0

    await pool._sweep_once()

    registry.has_pending_for_user.assert_awaited_once_with(1)
    assert 1 in pool._agents
    agent.retire.assert_not_awaited()


@pytest.mark.asyncio
async def test_sweep_once_leaves_agent_alone_that_became_busy_during_registry_await() -> None:
    registry = MagicMock()

    async def _has_pending(_telegram_user_id: int) -> bool:
        agent.is_busy = True
        return False

    registry.has_pending_for_user = AsyncMock(side_effect=_has_pending)
    pool = _make_pool(confirmation_registry=registry)
    fake_agent_cls = _fake_agent_class()

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        agent = _mock_agent(await pool.get_or_create(1))
    agent.idle_seconds = 9999.0

    before = metrics.agent_evictions_total.labels(reason="idle")._value.get()  # type: ignore[attr-defined]
    await pool._sweep_once()

    assert 1 in pool._agents
    agent.retire.assert_not_awaited()
    agent.mark_retired.assert_not_called()
    after = metrics.agent_evictions_total.labels(reason="idle")._value.get()  # type: ignore[attr-defined]
    assert after == before


@pytest.mark.asyncio
async def test_sweep_once_retires_when_no_pending_confirmation() -> None:
    registry = MagicMock()
    registry.has_pending_for_user = AsyncMock(return_value=False)
    pool = _make_pool(confirmation_registry=registry)
    fake_agent_cls = _fake_agent_class()

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        agent = _mock_agent(await pool.get_or_create(1))
    agent.idle_seconds = 9999.0

    await pool._sweep_once()
    await asyncio.gather(*pool._retiring)

    registry.has_pending_for_user.assert_awaited_once_with(1)
    assert 1 not in pool._agents
    agent.retire.assert_awaited_once()


@pytest.mark.asyncio
async def test_sweep_once_leaves_replacement_agent_alone_while_previous_retires() -> None:
    """The idle sweeper must respect the same one-retiring-per-user bound
    reset() enforces, not just detach anything idle."""
    pool = _make_pool()
    fake_agent_cls = _fake_agent_class()
    park = asyncio.Event()

    async def _parked_retire() -> None:
        await park.wait()

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        first = _mock_agent(await pool.get_or_create(1))
        first.retire.side_effect = _parked_retire

        result = await pool.reset(1)
        assert result == "dropped"

        second = _mock_agent(await pool.get_or_create(1))
        second.idle_seconds = 9999.0

        before = metrics.agent_evictions_total.labels(reason="idle")._value.get()  # type: ignore[attr-defined]
        await pool._sweep_once()

        assert 1 in pool._agents
        second.mark_retired.assert_not_called()
        after = metrics.agent_evictions_total.labels(reason="idle")._value.get()  # type: ignore[attr-defined]
        assert after == before

        [retiring_task] = list(pool._retiring)
        park.set()
        await retiring_task

        await pool._sweep_once()
        await asyncio.gather(*pool._retiring)

    assert 1 not in pool._agents


@pytest.mark.asyncio
async def test_sweep_once_leaves_fresh_agent_alone() -> None:
    pool = _make_pool()
    fake_agent_cls = _fake_agent_class()

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        agent = _mock_agent(await pool.get_or_create(1))

    await pool._sweep_once()

    assert 1 in pool._agents
    agent.retire.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_detaches_and_increments_counter_once() -> None:
    pool = _make_pool()
    fake_agent_cls = _fake_agent_class()

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        first = await pool.get_or_create(1)
        assert first is not None

        before = metrics.agent_evictions_total.labels(reason="reset")._value.get()  # type: ignore[attr-defined]
        result = await pool.reset(1)
        assert result == "dropped"
        assert 1 not in pool._agents
        after_first = metrics.agent_evictions_total.labels(reason="reset")._value.get()  # type: ignore[attr-defined]
        assert after_first == before + 1

        # The retirement task the reset above spawned is only scheduled, not
        # yet run (no real await has yielded to the event loop) — await it so
        # the next reset() reads "no retiring agent" rather than "retiring".
        await asyncio.gather(*pool._retiring)

        result_again = await pool.reset(1)
        assert result_again == "nothing"
        after_second = metrics.agent_evictions_total.labels(reason="reset")._value.get()  # type: ignore[attr-defined]
        assert after_second == after_first

        second = await pool.get_or_create(1)
        assert second is not None
        assert second is not first

    await asyncio.gather(*pool._retiring)


@pytest.mark.asyncio
async def test_reset_returns_retiring_while_previous_agent_still_stopping() -> None:
    pool = _make_pool()
    fake_agent_cls = _fake_agent_class()
    park = asyncio.Event()

    async def _parked_retire() -> None:
        await park.wait()

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        first = _mock_agent(await pool.get_or_create(1))
        first.retire.side_effect = _parked_retire

        before = metrics.agent_evictions_total.labels(reason="reset")._value.get()  # type: ignore[attr-defined]
        first_result = await pool.reset(1)
        assert first_result == "dropped"

        second = await pool.get_or_create(1)
        assert second is not None
        assert second is not first

        second_result = await pool.reset(1)
        assert second_result == "retiring"
        assert 1 in pool._agents
        after = metrics.agent_evictions_total.labels(reason="reset")._value.get()  # type: ignore[attr-defined]
        assert after == before + 1

        [retiring_task] = list(pool._retiring)
        park.set()
        await retiring_task

        third_result = await pool.reset(1)
        assert third_result == "dropped"

    await asyncio.gather(*pool._retiring)


@pytest.mark.asyncio
async def test_one_user_cannot_exceed_two_pool_slots() -> None:
    pool = _make_pool()
    fake_agent_cls = _fake_agent_class()
    park = asyncio.Event()

    async def _parked_retire() -> None:
        await park.wait()

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        first = _mock_agent(await pool.get_or_create(1))
        first.retire.side_effect = _parked_retire

        for _ in range(5):
            await pool.reset(1)
            await pool.get_or_create(1)
            assert len(pool._agents) + len(pool._retiring) == 2

    park.set()
    await asyncio.gather(*pool._retiring)


def test_idle_timeout_seconds_converts_minutes_to_seconds() -> None:
    pool = _make_pool_with_idle_timeout(30)
    assert pool._idle_timeout_seconds == 1800.0


@pytest.mark.asyncio
async def test_sweep_loop_logs_and_continues_when_sweep_once_raises_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected exception inside one sweep pass must be logged, not
    silently kill the background sweeper task."""
    pool = _make_pool()
    pool._idle_timeout_seconds = 0.1

    call_count = 0
    original = pool._sweep_once

    async def _flaky_sweep_once() -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("boom")
        await original()

    monkeypatch.setattr(pool, "_sweep_once", _flaky_sweep_once)

    await pool.start()
    for _ in range(80):
        if call_count >= 2:
            break
        await asyncio.sleep(0.05)
    await pool.stop()

    assert call_count >= 2


@pytest.mark.asyncio
async def test_cap_counts_retiring_agents() -> None:
    pool = _make_pool()
    fake_agent_cls = _fake_agent_class()
    park = asyncio.Event()

    async def _never_finishes() -> None:
        await park.wait()

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        for user_id in range(_POOL_MAX_AGENTS):
            await pool.get_or_create(user_id)
        for created in fake_agent_cls.created:
            created.retire.side_effect = _never_finishes

        await pool.reset(0)
        overflow = await pool.get_or_create(9999)

    assert overflow is None
    assert len(pool._retiring) == 1

    for task in pool._retiring:
        task.cancel()
    await asyncio.gather(*pool._retiring, return_exceptions=True)


@pytest.mark.asyncio
async def test_start_skips_sweeper_when_idle_eviction_disabled() -> None:
    pool = _make_pool_with_idle_timeout(0)

    await pool.start()

    assert pool._sweeper_task is None


@pytest.mark.asyncio
async def test_start_creates_sweeper_task_once_and_stop_cancels_it() -> None:
    pool = _make_pool_with_idle_timeout(30)

    await pool.start()
    task = pool._sweeper_task
    assert task is not None

    await pool.start()
    assert pool._sweeper_task is task

    await pool.stop()
    assert pool._sweeper_task is None
    assert task.cancelled()


@pytest.mark.asyncio
async def test_stop_stops_every_agent_and_clears_pool() -> None:
    pool = _make_pool()
    fake_agent_cls = _fake_agent_class()

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        agent_1 = await pool.get_or_create(1)
        agent_2 = await pool.get_or_create(2)

        await pool.stop()

    assert agent_1 is not None
    assert agent_2 is not None
    agent_1.stop.assert_awaited_once()  # type: ignore[attr-defined]
    agent_2.stop.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_stop_continues_past_a_failing_agent_and_reraises_first() -> None:
    pool = _make_pool()
    fake_agent_cls = _fake_agent_class()

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        agent_1 = _mock_agent(await pool.get_or_create(1))
        agent_2 = _mock_agent(await pool.get_or_create(2))
    error = RuntimeError("boom")
    agent_1.stop.side_effect = error

    with pytest.raises(RuntimeError) as excinfo:
        await pool.stop()

    assert excinfo.value is error
    agent_2.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_waits_for_outstanding_retirement_task() -> None:
    pool = _make_pool()
    fake_agent_cls = _fake_agent_class()
    finished = False

    async def _slow_retire() -> None:
        nonlocal finished
        await asyncio.sleep(0.05)
        finished = True

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        agent = _mock_agent(await pool.get_or_create(1))
        agent.retire.side_effect = _slow_retire

        await pool.reset(1)
        snapshot = set(pool._retiring)

        await pool.stop()

    assert finished is True
    assert all(task.done() for task in snapshot)


@pytest.mark.asyncio
async def test_retire_failure_does_not_stop_later_sweeps() -> None:
    pool = _make_pool()
    fake_agent_cls = _fake_agent_class()

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        agent_1 = _mock_agent(await pool.get_or_create(1))
        agent_2 = _mock_agent(await pool.get_or_create(2))
    agent_1.retire.side_effect = RuntimeError("boom")
    agent_1.idle_seconds = 9999.0
    agent_2.idle_seconds = 0.0

    await pool._sweep_once()
    await asyncio.gather(*pool._retiring)

    assert 1 not in pool._agents

    agent_2.idle_seconds = 9999.0
    await pool._sweep_once()
    await asyncio.gather(*pool._retiring)

    assert 2 not in pool._agents


@pytest.mark.asyncio
async def test_stop_then_get_or_create_starts_a_fresh_agent() -> None:
    pool = _make_pool()
    fake_agent_cls = _fake_agent_class()

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        first = await pool.get_or_create(1)
        await pool.stop()
        second = await pool.get_or_create(1)

    assert first is not second
