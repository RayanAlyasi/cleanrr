from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from claude_agent_sdk import SdkMcpTool, tool

import cleanrr.metrics as metrics
from cleanrr.config import Settings
from cleanrr.tools._context import current_telegram_user_id
from cleanrr.tools._qbittorrent_auth import QbitAuthError, fetch_torrents, login
from cleanrr.tools._results import text_result

logger = logging.getLogger(__name__)

# error/missingFiles are qBittorrent's genuinely-stuck states (disk write
# failure, files deleted externally) — not just "no peers right now" like
# stalledDL/metaDL, but the ones most needing admin attention.
_STALLED_STATES = frozenset({"stalledDL", "metaDL", "error", "missingFiles"})


def _format_age(ts: int) -> str:
    if ts == 0:
        return "unknown"
    elapsed = int(time.time()) - ts
    if elapsed < 3600:
        return f"{elapsed // 60}m"
    if elapsed < 86400:
        return f"{elapsed // 3600}h"
    return f"{elapsed // 86400}d"


def build_tools(qbit_client: httpx.AsyncClient, settings: Settings) -> list[SdkMcpTool]:
    """Factory for qBittorrent tools."""

    @tool(
        "list_stalled_torrents",
        "List torrents currently stalled or stuck in qBittorrent. Admin-only. "
        "Use when the admin asks 'what's stalled?', 'any stuck downloads?'.",
        {},
    )
    async def list_stalled_torrents(_args: dict[str, Any]) -> dict[str, Any]:
        if (
            settings.qbittorrent_url is None
            or settings.qbittorrent_username is None
            or settings.qbittorrent_password is None
        ):
            metrics.tool_calls_total.labels(
                tool="list_stalled_torrents", status="qbittorrent_not_configured"
            ).inc()
            return text_result(
                "qBittorrent isn't configured — ask the admin to set QBITTORRENT_URL, "
                "QBITTORRENT_USERNAME, and QBITTORRENT_PASSWORD.",
                is_error=True,
            )

        try:
            caller_id = current_telegram_user_id.get()
        except LookupError:
            metrics.tool_calls_total.labels(
                tool="list_stalled_torrents", status="context_missing"
            ).inc()
            return text_result("Internal error — user context unavailable.", is_error=True)

        if caller_id not in settings.admin_telegram_ids:
            metrics.tool_calls_total.labels(tool="list_stalled_torrents", status="not_admin").inc()
            return text_result("Only the admin can check stalled torrents.", is_error=False)

        base_url = str(settings.qbittorrent_url).rstrip("/")

        try:
            await login(qbit_client, base_url, settings)
        except QbitAuthError:
            logger.exception("qBittorrent login failed")
            metrics.tool_calls_total.labels(
                tool="list_stalled_torrents", status="auth_failed"
            ).inc()
            return text_result(
                "qBittorrent auth failed — check QBITTORRENT_USERNAME and QBITTORRENT_PASSWORD.",
                is_error=True,
            )

        try:
            torrents, needs_reauth = await fetch_torrents(qbit_client, base_url)
        except httpx.HTTPError:
            logger.exception("qBittorrent HTTP error fetching torrents")
            metrics.tool_calls_total.labels(tool="list_stalled_torrents", status="http_error").inc()
            return text_result("qBittorrent unreachable — try again in a moment.", is_error=True)
        except ValueError:
            metrics.tool_calls_total.labels(
                tool="list_stalled_torrents", status="parse_error"
            ).inc()
            return text_result(
                "Unexpected response from qBittorrent — try again later.", is_error=True
            )

        if needs_reauth:
            try:
                await login(qbit_client, base_url, settings)
            except QbitAuthError:
                logger.exception("qBittorrent re-login failed")
                metrics.tool_calls_total.labels(
                    tool="list_stalled_torrents", status="auth_failed"
                ).inc()
                return text_result(
                    "qBittorrent auth failed — check QBITTORRENT_USERNAME"
                    " and QBITTORRENT_PASSWORD.",
                    is_error=True,
                )

            try:
                torrents, still_needs_reauth = await fetch_torrents(qbit_client, base_url)
            except httpx.HTTPError:
                logger.exception("qBittorrent HTTP error on retry")
                metrics.tool_calls_total.labels(
                    tool="list_stalled_torrents", status="http_error"
                ).inc()
                return text_result(
                    "qBittorrent unreachable — try again in a moment.", is_error=True
                )
            except ValueError:
                metrics.tool_calls_total.labels(
                    tool="list_stalled_torrents", status="parse_error"
                ).inc()
                return text_result(
                    "Unexpected response from qBittorrent — try again later.", is_error=True
                )
            # A second 403 right after a successful re-login means the
            # session isn't sticking (cookie not being sent/accepted) — that's
            # a real failure, not "no torrents": don't report a false "clean".
            if still_needs_reauth:
                logger.error("qBittorrent still returning 403 after re-login")
                metrics.tool_calls_total.labels(
                    tool="list_stalled_torrents", status="auth_failed"
                ).inc()
                return text_result(
                    "qBittorrent auth failed — check QBITTORRENT_USERNAME"
                    " and QBITTORRENT_PASSWORD.",
                    is_error=True,
                )

        stalled = [t for t in torrents if t.get("state") in _STALLED_STATES][:10]

        if not stalled:
            metrics.tool_calls_total.labels(tool="list_stalled_torrents", status="success").inc()
            return text_result("No stalled torrents right now.", is_error=False)

        lines: list[str] = [f"Stalled torrents ({len(stalled)}):"]
        for t in stalled:
            # Torrent name is set by the torrent's creator — untrusted input;
            # truncate to keep the aggregate reply within Telegram's message limit.
            name = str(t.get("name", "unknown"))[:80]
            state = t.get("state", "unknown")
            size_bytes = t.get("size", 0)
            progress = t.get("progress", 0.0)
            last_activity = t.get("last_activity", 0)
            added_on = t.get("added_on", 0)

            age_ts = last_activity if last_activity else added_on
            age = _format_age(int(age_ts))

            size_gb = int(size_bytes) / 1_073_741_824
            pct = int(float(progress) * 100)

            lines.append(f"- {name} [{state}] {pct}% of {size_gb:.1f} GB — idle {age}")

        metrics.tool_calls_total.labels(tool="list_stalled_torrents", status="success").inc()
        return text_result("\n".join(lines), is_error=False)

    return [list_stalled_torrents]
