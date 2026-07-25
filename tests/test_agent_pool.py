from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from cleanrr.agent_pool import _POOL_MAX_AGENTS, AgentPool
from cleanrr.config import Settings
from cleanrr.identity import Identity


def _settings() -> Settings:
    return Settings(
        telegram_bot_token=SecretStr("test"),
        anthropic_api_key=SecretStr("sk-test"),
    )


def _make_pool(**overrides: object) -> AgentPool:
    kwargs: dict[str, object] = {
        "identity": MagicMock(spec=Identity),
        "settings": _settings(),
        "timeout_seconds": 5.0,
    }
    kwargs.update(overrides)
    return AgentPool(**kwargs)  # type: ignore[arg-type]


def _fake_agent_class() -> MagicMock:
    """Stand-in for cleanrr.agent.Agent — each call returns a fresh mock
    instance with AsyncMock start/stop, so tests exercise AgentPool's own
    logic without spawning a real subprocess."""
    created: list[MagicMock] = []

    def _construct(**_kwargs: object) -> MagicMock:
        instance = MagicMock()
        instance.start = AsyncMock()
        instance.stop = AsyncMock()
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
async def test_stop_then_get_or_create_starts_a_fresh_agent() -> None:
    pool = _make_pool()
    fake_agent_cls = _fake_agent_class()

    with patch("cleanrr.agent_pool.Agent", fake_agent_cls):
        first = await pool.get_or_create(1)
        await pool.stop()
        second = await pool.get_or_create(1)

    assert first is not second
