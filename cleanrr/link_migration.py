from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from cleanrr.config import Settings
from cleanrr.identity import Identity, LinkedUser
from cleanrr.tools._user_request import _resolve_user_id

logger = logging.getLogger(__name__)

_BACKFILL_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class BackfillResult:
    migrated: int
    remaining: int


async def backfill_overseerr_user_ids(
    identity: Identity, client: httpx.AsyncClient | None, settings: Settings
) -> BackfillResult:
    """Resolve and record the Overseerr user id for links still keyed by username.

    Runs once at startup. A row that can't be resolved is never deleted or
    rewritten — it stays on the username lookup and is retried on the next
    start (or self-heals on the next tool call). Never raises: the caller
    bounds this with asyncio.wait_for and must still start the bot.
    """
    pending = await identity.links_needing_overseerr_user_id()
    if not pending:
        return BackfillResult(0, 0)

    if client is None or settings.overseerr_url is None or settings.overseerr_api_key is None:
        logger.warning(
            "link migration skipped: Overseerr is not configured; %d link(s) still keyed by"
            " username",
            len(pending),
        )
        return BackfillResult(0, len(pending))

    base_url = str(settings.overseerr_url).rstrip("/")

    by_username: dict[str, list[LinkedUser]] = {}
    for row in pending:
        by_username.setdefault(row.overseerr_username, []).append(row)

    migrated = 0
    for username, rows in by_username.items():
        # Non-printable chars can't reach a log line — same guard as
        # cleanrr/tools/_user_request.py's _resolve_user_id.
        safe_username = "".join(c for c in username if c.isprintable())[:32]
        try:
            user_id, status = await _resolve_user_id(client, base_url, username)
        except Exception:
            logger.warning(
                "link migration: resolving overseerr user @%s failed", safe_username, exc_info=True
            )
            continue
        if user_id is None:
            if status == "user_not_found":
                logger.warning(
                    "link migration: no single overseerr user matches @%s exactly; %d link(s)"
                    " cannot reach their requests until re-invited with /invite",
                    safe_username,
                    len(rows),
                )
            else:
                logger.warning(
                    "link migration: couldn't resolve overseerr user @%s (%s); %d link(s) stay on"
                    " username lookup until it resolves",
                    safe_username,
                    status,
                    len(rows),
                )
            continue
        for row in rows:
            if await identity.record_overseerr_user_id(row, user_id):
                migrated += 1

    remaining = len(pending) - migrated
    logger.info(
        "link migration: keyed %d link(s) by overseerr user id, %d still unresolved",
        migrated,
        remaining,
    )
    return BackfillResult(migrated, remaining)
