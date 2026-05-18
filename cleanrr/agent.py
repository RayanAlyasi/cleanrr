from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    TextBlock,
)

DEFAULT_SYSTEM_PROMPT = (
    "You are cleanrr, a Telegram assistant. Right now you can only chat — "
    "tools for diagnosing the user's Sonarr/Radarr/Overseerr/qBittorrent stack "
    "land in upcoming phases."
)


class Agent:
    """Long-lived wrapper around ClaudeSDKClient that routes per-user messages by session_id."""

    def __init__(self, *, model: str = "sonnet", system_prompt: str | None = None) -> None:
        self._options = ClaudeAgentOptions(
            model=model,
            system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT,
        )
        self._client: ClaudeSDKClient | None = None
        self._stack: AsyncExitStack | None = None
        # The SDK fronts one CLI subprocess per client; overlapping queries
        # would interleave on the shared response stream. Serialize them.
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._client is not None:
            return
        stack = AsyncExitStack()
        self._client = await stack.enter_async_context(
            ClaudeSDKClient(options=self._options)
        )
        self._stack = stack

    async def stop(self) -> None:
        if self._stack is None:
            return
        await self._stack.aclose()
        self._stack = None
        self._client = None

    async def respond(self, *, session_id: str, prompt: str) -> str:
        if self._client is None:
            raise RuntimeError("Agent.start() must be called before respond()")

        async with self._lock:
            await self._client.query(prompt, session_id=session_id)
            chunks: list[str] = []
            async for message in self._client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            chunks.append(block.text)
            return "".join(chunks).strip()
