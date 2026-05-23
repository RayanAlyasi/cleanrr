from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

import httpx
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    TextBlock,
    create_sdk_mcp_server,
)

from cleanrr.config import Settings
from cleanrr.identity import Identity
from cleanrr.tools._context import current_telegram_user_id
from cleanrr.tools.overseerr import build_tools as build_overseerr_tools
from cleanrr.tools.radarr import build_tools as build_radarr_tools
from cleanrr.tools.sonarr import build_tools as build_sonarr_tools

DEFAULT_SYSTEM_PROMPT = """\
You are cleanrr, a Telegram bot for a self-hosted media homelab
(Plex/Jellyfin alongside Sonarr, Radarr, Overseerr, qBittorrent).

## Role
Help friends and family of the homelab admin diagnose and resolve
issues with their media requests — "where's my movie?", "why is this
stuck?", "can you clean up some space?".

## Reply style
Brief: 1-3 sentences, plain text, no markdown. Conversational, not
formal. Match the user's language and tone.

## Scope (current phase)
Tools are available to look up request status. Diagnosis and fix
actions land in later phases. Don't promise actions you can't take.

## Tools available
- `list_my_requests` — show the user's full Overseerr request list. Use when they ask
  for everything ("what did I request?", "show me my requests").
- `find_my_request` — look up ONE specific title. Use when they ask about a single
  movie/show ("is Dune ready?", "what's the status of Severance?"). Pass the title
  exactly as the user wrote it.
- `get_show_status` — look up TV show download status in Sonarr (episodes ready,
  downloading). Use when they ask about show progress ("is The Bear downloading?",
  "how many episodes are ready?").
- `get_movie_status` — look up movie download status in Radarr (downloaded vs downloading
  vs nothing yet). Use when they ask about a specific film ("is Dune ready?", "where's
  my Batman movie?").
"""


class Agent:
    """Long-lived wrapper around ClaudeSDKClient that routes per-user messages by session_id."""

    def __init__(
        self,
        *,
        identity: Identity,
        settings: Settings,
        model: str = "sonnet",
        system_prompt: str | None = None,
        timeout_seconds: float,
    ) -> None:
        self._identity = identity
        self._settings = settings
        self._options = ClaudeAgentOptions(
            model=model,
            system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT,
        )
        self._timeout_seconds = timeout_seconds
        self._client: ClaudeSDKClient | None = None
        self._stack: AsyncExitStack | None = None
        # The SDK fronts one CLI subprocess per client; overlapping queries
        # would interleave on the shared response stream. Serialize them.
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._client is not None:
            return
        stack = AsyncExitStack()
        settings = self._settings

        overseerr_client: httpx.AsyncClient | None = None
        if settings.overseerr_url is not None and settings.overseerr_api_key is not None:
            overseerr_client = await stack.enter_async_context(
                httpx.AsyncClient(
                    headers={"X-Api-Key": settings.overseerr_api_key.get_secret_value()},
                    timeout=settings.overseerr_timeout_seconds,
                )
            )

        sonarr_client: httpx.AsyncClient | None = None
        if settings.sonarr_url is not None and settings.sonarr_api_key is not None:
            sonarr_client = await stack.enter_async_context(
                httpx.AsyncClient(
                    headers={"X-Api-Key": settings.sonarr_api_key.get_secret_value()},
                    timeout=settings.sonarr_timeout_seconds,
                )
            )

        radarr_client: httpx.AsyncClient | None = None
        if settings.radarr_url is not None and settings.radarr_api_key is not None:
            radarr_client = await stack.enter_async_context(
                httpx.AsyncClient(
                    headers={"X-Api-Key": settings.radarr_api_key.get_secret_value()},
                    timeout=settings.radarr_timeout_seconds,
                )
            )

        tools = (
            build_overseerr_tools(overseerr_client, self._identity, settings)
            if overseerr_client is not None
            else []
        )

        if (
            sonarr_client is not None
            and overseerr_client is not None
            and settings.sonarr_url is not None
            and settings.sonarr_api_key is not None
            and settings.overseerr_url is not None
            and settings.overseerr_api_key is not None
        ):
            sonarr_tools = build_sonarr_tools(
                sonarr_client, overseerr_client, self._identity, settings
            )
            tools.extend(sonarr_tools)

        if (
            radarr_client is not None
            and overseerr_client is not None
            and settings.radarr_url is not None
            and settings.radarr_api_key is not None
            and settings.overseerr_url is not None
            and settings.overseerr_api_key is not None
        ):
            radarr_tools = build_radarr_tools(
                radarr_client, overseerr_client, self._identity, settings
            )
            tools.extend(radarr_tools)

        mcp = create_sdk_mcp_server(name="cleanrr", tools=tools)
        self._options.mcp_servers = {"cleanrr": mcp}
        self._options.allowed_tools = [t.name for t in tools]
        self._options.tools = []
        self._options.permission_mode = "dontAsk"
        self._options.strict_mcp_config = True

        self._client = await stack.enter_async_context(ClaudeSDKClient(options=self._options))
        self._stack = stack

    async def stop(self) -> None:
        if self._stack is None:
            return
        await self._stack.aclose()
        self._stack = None
        self._client = None

    async def respond(self, *, telegram_user_id: int, prompt: str) -> str:
        if self._client is None:
            raise RuntimeError("Agent.start() must be called before respond()")

        session_id = f"telegram_{telegram_user_id}"

        async def _query() -> str:
            await self._client.query(prompt, session_id=session_id)  # type: ignore[union-attr]
            chunks: list[str] = []
            async for message in self._client.receive_response():  # type: ignore[union-attr]
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            chunks.append(block.text)
            return "".join(chunks).strip()

        token = current_telegram_user_id.set(telegram_user_id)
        try:
            # Acquire lock first; timeout fires inside the lock. This assumes the SDK's
            # receive_response() propagates CancelledError on timeout — if it swallows it,
            # the lock may not be released. The mocked test verifies this contract with the
            # mocked SDK; real-world coverage requires integration testing.
            async with self._lock:
                return await asyncio.wait_for(_query(), timeout=self._timeout_seconds)
        finally:
            current_telegram_user_id.reset(token)
