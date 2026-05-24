from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from claude_agent_sdk import SdkMcpTool, tool

import cleanrr.metrics
from cleanrr.config import Settings
from cleanrr.tools._context import current_telegram_user_id
from cleanrr.tools._results import text_result

logger = logging.getLogger(__name__)

_STALLED_STATES = frozenset({"stalledDL", "metaDL"})


class _QbitAuthError(Exception):
    pass


async def _login(client: httpx.AsyncClient, base_url: str, settings: Settings) -> None:
    password = settings.qbittorrent_password
    if password is None:
        raise _QbitAuthError("no password configured")
    try:
        resp = await client.post(
            f"{base_url}/api/v2/auth/login",
            data={
                "username": settings.qbittorrent_username,
                "password": password.get_secret_value(),
            },
        )
    except httpx.HTTPError as exc:
        raise _QbitAuthError(str(exc)) from exc

    if resp.status_code != 200 or resp.text.strip() != "Ok.":
        raise _QbitAuthError(f"login rejected (status={resp.status_code})")


async def _fetch_torrents(
    client: httpx.AsyncClient, base_url: str
) -> tuple[list[dict[str, Any]], bool]:
    """Return (torrent_list, needs_reauth).

    needs_reauth is True when the server returned 403.
    """
    resp = await client.get(f"{base_url}/api/v2/torrents/info")
    if resp.status_code == 403:
        return [], True
    if resp.status_code != 200:
        raise httpx.HTTPStatusError(
            f"unexpected status {resp.status_code}",
            request=resp.request,
            response=resp,
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise ValueError("parse_error") from exc
    if not isinstance(data, list):
        raise ValueError("parse_error")
    return data, False


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
            cleanrr.metrics.tool_calls_total.labels(
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
            cleanrr.metrics.tool_calls_total.labels(
                tool="list_stalled_torrents", status="context_missing"
            ).inc()
            return text_result("Internal error — user context unavailable.", is_error=True)

        if caller_id not in settings.admin_telegram_ids:
            cleanrr.metrics.tool_calls_total.labels(
                tool="list_stalled_torrents", status="not_admin"
            ).inc()
            return text_result("Only the admin can check stalled torrents.", is_error=False)

        base_url = str(settings.qbittorrent_url).rstrip("/")

        try:
            await _login(qbit_client, base_url, settings)
        except _QbitAuthError:
            logger.exception("qBittorrent login failed")
            cleanrr.metrics.tool_calls_total.labels(
                tool="list_stalled_torrents", status="auth_failed"
            ).inc()
            return text_result(
                "qBittorrent auth failed — check QBITTORRENT_USERNAME and QBITTORRENT_PASSWORD.",
                is_error=True,
            )

        try:
            torrents, needs_reauth = await _fetch_torrents(qbit_client, base_url)
        except httpx.HTTPError:
            logger.exception("qBittorrent HTTP error fetching torrents")
            cleanrr.metrics.tool_calls_total.labels(
                tool="list_stalled_torrents", status="http_error"
            ).inc()
            return text_result("qBittorrent unreachable — try again in a moment.", is_error=True)
        except ValueError:
            cleanrr.metrics.tool_calls_total.labels(
                tool="list_stalled_torrents", status="parse_error"
            ).inc()
            return text_result(
                "Unexpected response from qBittorrent — try again later.", is_error=True
            )

        if needs_reauth:
            try:
                await _login(qbit_client, base_url, settings)
            except _QbitAuthError:
                logger.exception("qBittorrent re-login failed")
                cleanrr.metrics.tool_calls_total.labels(
                    tool="list_stalled_torrents", status="auth_failed"
                ).inc()
                return text_result(
                    "qBittorrent auth failed — check QBITTORRENT_USERNAME"
                    " and QBITTORRENT_PASSWORD.",
                    is_error=True,
                )

            try:
                torrents, _ = await _fetch_torrents(qbit_client, base_url)
            except httpx.HTTPError:
                logger.exception("qBittorrent HTTP error on retry")
                cleanrr.metrics.tool_calls_total.labels(
                    tool="list_stalled_torrents", status="http_error"
                ).inc()
                return text_result(
                    "qBittorrent unreachable — try again in a moment.", is_error=True
                )
            except ValueError:
                cleanrr.metrics.tool_calls_total.labels(
                    tool="list_stalled_torrents", status="parse_error"
                ).inc()
                return text_result(
                    "Unexpected response from qBittorrent — try again later.", is_error=True
                )

        stalled = [t for t in torrents if t.get("state") in _STALLED_STATES][:10]

        if not stalled:
            cleanrr.metrics.tool_calls_total.labels(
                tool="list_stalled_torrents", status="success"
            ).inc()
            return text_result("No stalled torrents right now.", is_error=False)

        lines: list[str] = [f"Stalled torrents ({len(stalled)}):"]
        for t in stalled:
            name = t.get("name", "unknown")
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

        cleanrr.metrics.tool_calls_total.labels(
            tool="list_stalled_torrents", status="success"
        ).inc()
        return text_result("\n".join(lines), is_error=False)

    return [list_stalled_torrents]
