from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx

from cleanrr.agent import Agent
from cleanrr.config import Settings
from cleanrr.identity import Identity
from cleanrr.permissions import ConfirmationRegistry

if TYPE_CHECKING:
    import telegram

logger = logging.getLogger(__name__)

# Mirrors ConfirmationRegistry._REGISTRY_MAX_ENTRIES' role: a small, fixed cap
# on concurrent per-user Agent subprocesses. Sized for a homelab's friends-
# and-family user base, not a multi-tenant service. No idle-eviction sweeper
# for v1 — revisit only if real usage shows the cap matters.
_POOL_MAX_AGENTS = 15


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

    async def get_or_create(self, telegram_user_id: int) -> Agent | None:
        """Return this user's Agent, starting one on their first message.

        Returns None if the pool is full and this is a user with no
        existing Agent — the caller should reply with a friendly
        at-capacity message rather than starting a subprocess unbounded.
        """
        async with self._lock:
            existing = self._agents.get(telegram_user_id)
            if existing is not None:
                return existing
            if len(self._agents) >= _POOL_MAX_AGENTS:
                logger.warning(
                    "agent pool full (%d agents); refusing new user %s",
                    _POOL_MAX_AGENTS,
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
            return agent

    async def stop(self) -> None:
        agents = list(self._agents.values())
        self._agents.clear()
        for agent in agents:
            await agent.stop()
