from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Literal

import httpx

import cleanrr.metrics as metrics
from cleanrr.agent import Agent
from cleanrr.config import Settings
from cleanrr.identity import Identity
from cleanrr.permissions import ConfirmationRegistry

if TYPE_CHECKING:
    import telegram

logger = logging.getLogger(__name__)

# Mirrors ConfirmationRegistry._REGISTRY_MAX_ENTRIES' role: a small, fixed cap
# on concurrent per-user Agent subprocesses. Sized for a homelab's friends-
# and-family user base, not a multi-tenant service. Counts Agents that are
# still stopping too, because their subprocess is alive for up to 20s.
_POOL_MAX_AGENTS = 15

# Single source of truth for the agent_evictions_total{reason=...} label.
# Prometheus does not validate label values at runtime, so any code stamping
# the metric with a value outside this literal will silently inflate
# cardinality.
EvictionReason = Literal["idle", "reset"]

# One close() is ~20s; retirement tasks are waited on rather than cancelled
# because the SDK's close() must not be cancelled (see Agent.stop()).
_SHUTDOWN_RETIRE_WAIT_SECONDS = 30.0


class AgentPool:
    """One Agent (one CLI subprocess) per telegram_user_id, created lazily.

    Replaces the single shared Agent every user used to serialize through.
    A confirmation prompt pending on one user's Agent holds only that
    Agent's own lock, so it can never block another user's respond().
    """

    def __init__(
        self,
        *,
        identity: Identity,
        settings: Settings,
        model: str = "sonnet",
        system_prompt: str | None = None,
        timeout_seconds: float,
        telegram_bot: telegram.Bot | None = None,
        overseerr_client: httpx.AsyncClient | None = None,
        sonarr_client: httpx.AsyncClient | None = None,
        radarr_client: httpx.AsyncClient | None = None,
        qbit_client: httpx.AsyncClient | None = None,
        confirmation_registry: ConfirmationRegistry | None = None,
    ) -> None:
        self._identity = identity
        self._settings = settings
        self._model = model
        self._system_prompt = system_prompt
        self._timeout_seconds = timeout_seconds
        self._telegram_bot = telegram_bot
        self._overseerr_client = overseerr_client
        self._sonarr_client = sonarr_client
        self._radarr_client = radarr_client
        self._qbit_client = qbit_client
        self._confirmation_registry = confirmation_registry
        self._agents: dict[int, Agent] = {}
        self._lock = asyncio.Lock()
        self._retiring: set[asyncio.Task[None]] = set()
        self._sweeper_task: asyncio.Task[None] | None = None
        self._idle_timeout_seconds = float(settings.agent_idle_timeout_minutes) * 60

    async def get_or_create(self, telegram_user_id: int) -> Agent | None:
        """Return this user's Agent, starting one on their first message.

        Returns None if the pool is full and this is a user with no
        existing Agent — the caller should reply with a friendly
        at-capacity message rather than starting a subprocess unbounded.
        """
        async with self._lock:
            existing = self._agents.get(telegram_user_id)
            if existing is not None:
                existing.touch()
                return existing
            if len(self._agents) + len(self._retiring) >= _POOL_MAX_AGENTS:
                logger.warning(
                    "agent pool full (%d agents, %d retiring); refusing new user %s",
                    len(self._agents),
                    len(self._retiring),
                    telegram_user_id,
                )
                return None
            agent = Agent(
                identity=self._identity,
                settings=self._settings,
                model=self._model,
                system_prompt=self._system_prompt,
                timeout_seconds=self._timeout_seconds,
                telegram_bot=self._telegram_bot,
                overseerr_client=self._overseerr_client,
                sonarr_client=self._sonarr_client,
                radarr_client=self._radarr_client,
                qbit_client=self._qbit_client,
                confirmation_registry=self._confirmation_registry,
            )
            await agent.start(telegram_user_id)
            self._agents[telegram_user_id] = agent
            agent.touch()
            self._set_gauge_locked()
            return agent

    def _set_gauge_locked(self) -> None:
        metrics.agent_pool_agents.set(len(self._agents))

    def _detach_locked(self, telegram_user_id: int, agent: Agent, reason: EvictionReason) -> None:
        # Marked retired first, synchronously, so a turn still winding down
        # cannot reserve a new confirmation after this detach.
        agent.mark_retired()
        del self._agents[telegram_user_id]
        self._set_gauge_locked()
        metrics.agent_evictions_total.labels(reason=reason).inc()
        logger.info("evicting user %s's agent (reason=%s)", telegram_user_id, reason)
        # No await here: the pool lock is never held across a stop.
        task = asyncio.create_task(self._retire(agent))
        self._retiring.add(task)
        task.add_done_callback(self._retiring.discard)

    async def _retire(self, agent: Agent) -> None:
        try:
            await agent.retire()
        except Exception:
            logger.exception("stopping a retired agent failed")

    async def reset(self, telegram_user_id: int) -> bool:
        async with self._lock:
            agent = self._agents.get(telegram_user_id)
            if agent is None:
                return False
            self._detach_locked(telegram_user_id, agent, "reset")
            return True

    async def _sweep_once(self) -> None:
        # The pool lock is taken before the registry lock, never the other
        # way round, and the registry never awaits I/O under its own lock —
        # which is also why the pool dict itself cannot change across the
        # registry await below, so no identity re-check of self._agents[uid]
        # is needed.
        async with self._lock:
            idle = [
                (telegram_user_id, agent)
                for telegram_user_id, agent in self._agents.items()
                if agent.idle_seconds >= self._idle_timeout_seconds and not agent.is_busy
            ]
            registry = self._confirmation_registry
            for telegram_user_id, agent in idle:
                # A pending confirmation normally implies a held lock, but a
                # turn that already timed out released the lock while its
                # registry entry lives until the TTL — so this check earns
                # its keep even though it looks redundant with the is_busy
                # filter above.
                if registry is not None and await registry.has_pending_for_user(telegram_user_id):
                    logger.debug(
                        "skipping idle eviction for user %s: confirmation pending",
                        telegram_user_id,
                    )
                    continue
                # Load-bearing, not defensive noise: the registry await above
                # yields, and a handler already parked on this Agent's own
                # lock (respond() waits on Agent._lock with no pool lock —
                # agent.py:419) can acquire it during that yield, because
                # asyncio.Lock.release() clears locked() before the waiter is
                # scheduled. Without this re-check, a second message queued
                # behind a turn longer than the idle window would be detached
                # mid-turn and run retired, so can_use_tool refuses its
                # destructive tools with "the user reset this conversation"
                # when nobody reset. Nothing may await between here and
                # _detach_locked — mark_retired() and the del are both
                # synchronous.
                if agent.is_busy:
                    logger.debug(
                        "skipping idle eviction for user %s: became busy",
                        telegram_user_id,
                    )
                    continue
                self._detach_locked(telegram_user_id, agent, "idle")

    async def _sweep_loop(self) -> None:
        interval = max(self._idle_timeout_seconds / 2, 1.0)
        while True:
            await asyncio.sleep(interval)
            try:
                await self._sweep_once()
            except Exception:
                # Unlike the registry's TTL sweep there is no lazy fallback
                # path, so a sweeper that exited on one bad iteration would
                # silently leak subprocesses.
                logger.exception("agent pool sweep failed; continuing")

    async def start(self) -> None:
        if self._idle_timeout_seconds <= 0:
            logger.info("idle eviction disabled (AGENT_IDLE_TIMEOUT_MINUTES=0)")
            return
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
                logger.exception("agent pool sweeper crashed on shutdown")

        async with self._lock:
            agents = list(self._agents.values())
            self._agents.clear()
            self._set_gauge_locked()

        first_error: Exception | None = None
        for agent in agents:
            try:
                await agent.stop()
            except Exception as exc:
                logger.exception("stopping an agent during shutdown failed")
                if first_error is None:
                    first_error = exc

        if self._retiring:
            _, pending = await asyncio.wait(
                set(self._retiring), timeout=_SHUTDOWN_RETIRE_WAIT_SECONDS
            )
            if pending:
                logger.warning(
                    "%d agent retirement task(s) still running at shutdown; left running",
                    len(pending),
                )

        if first_error is not None:
            raise first_error
