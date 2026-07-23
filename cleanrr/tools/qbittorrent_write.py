"""Destructive qBittorrent tools — invoked behind the can_use_tool confirmation gate."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from claude_agent_sdk import SdkMcpTool, tool

import cleanrr.metrics as metrics
from cleanrr.config import Settings
from cleanrr.tools._context import current_telegram_user_id
from cleanrr.tools._qbittorrent_auth import QbitAuthError, fetch_torrents, login
from cleanrr.tools._results import text_result

logger = logging.getLogger(__name__)


def build_tools(qbit_client: httpx.AsyncClient, settings: Settings) -> list[SdkMcpTool]:
    """Factory for destructive qBittorrent tools.

    Admin-only. cleanrr.permissions.ADMIN_ONLY_TOOLS denies non-admins before
    any confirmation prompt is sent. The check below is a second, independent
    enforcement point — defense in depth, not the primary gate — so a bug in
    the permission callback can't let a non-admin mutation through.
    """

    @tool(
        "delete_torrent",
        "Permanently delete a torrent AND its downloaded files from qBittorrent. "
        "Admin-only. Pass the torrent's hash (the long hex string from "
        "list_stalled_torrents). Destructive: the admin confirms in chat first. "
        "Use for torrents wedged with no recovery path.",
        {"torrent_hash": str},
    )
    async def delete_torrent(args: dict[str, Any]) -> dict[str, Any]:
        if (
            settings.qbittorrent_url is None
            or settings.qbittorrent_username is None
            or settings.qbittorrent_password is None
        ):
            metrics.tool_calls_total.labels(
                tool="delete_torrent", status="qbittorrent_not_configured"
            ).inc()
            return text_result(
                "qBittorrent isn't configured — ask the admin to set QBITTORRENT_URL, "
                "QBITTORRENT_USERNAME, and QBITTORRENT_PASSWORD.",
                is_error=True,
            )

        torrent_hash = args.get("torrent_hash", "")
        if not isinstance(torrent_hash, str) or not torrent_hash.strip():
            metrics.tool_calls_total.labels(tool="delete_torrent", status="bad_args").inc()
            return text_result("Bad torrent hash.", is_error=True)
        torrent_hash = torrent_hash.strip().lower()
        # qBit hashes are 40-char SHA-1 hex. Reject anything else so a hostile
        # arg can't be smuggled into the hashes= form field.
        if len(torrent_hash) != 40 or not all(c in "0123456789abcdef" for c in torrent_hash):
            metrics.tool_calls_total.labels(tool="delete_torrent", status="bad_args").inc()
            return text_result("Bad torrent hash.", is_error=True)

        try:
            caller_id = current_telegram_user_id.get()
        except LookupError:
            metrics.tool_calls_total.labels(tool="delete_torrent", status="context_missing").inc()
            return text_result("Internal error — user context unavailable.", is_error=True)

        if caller_id not in settings.admin_telegram_ids:
            # Admin gate is a pre-confirmation guard, not a confirmation outcome,
            # so it stays out of destructive_actions_total (which is locked to the
            # Outcome literal). tool_calls_total carries the unauthorized signal.
            metrics.tool_calls_total.labels(tool="delete_torrent", status="unauthorized").inc()
            return text_result("Only the admin can delete torrents.", is_error=True)

        base_url = str(settings.qbittorrent_url).rstrip("/")

        try:
            await login(qbit_client, base_url, settings)
        except QbitAuthError:
            logger.exception("qBittorrent login failed in delete_torrent")
            metrics.tool_calls_total.labels(tool="delete_torrent", status="auth_failed").inc()
            return text_result(
                "qBittorrent auth failed — check QBITTORRENT_USERNAME and QBITTORRENT_PASSWORD.",
                is_error=True,
            )

        # Capture name BEFORE delete so we can log it on success even after
        # the torrent is gone.
        try:
            torrents_before, needs_reauth = await fetch_torrents(
                qbit_client, base_url, hashes=torrent_hash
            )
        except httpx.HTTPError:
            logger.exception("qBittorrent HTTP error fetching torrent pre-delete")
            metrics.tool_calls_total.labels(tool="delete_torrent", status="http_error").inc()
            return text_result("qBittorrent unreachable — try again in a moment.", is_error=True)
        except ValueError:
            metrics.tool_calls_total.labels(tool="delete_torrent", status="parse_error").inc()
            return text_result(
                "Unexpected response from qBittorrent — try again later.", is_error=True
            )

        if needs_reauth:
            metrics.tool_calls_total.labels(tool="delete_torrent", status="auth_failed").inc()
            return text_result("qBittorrent session expired — try again.", is_error=True)

        if not torrents_before:
            metrics.tool_calls_total.labels(tool="delete_torrent", status="not_found").inc()
            return text_result("No torrent with that hash.", is_error=False)

        torrent_name = str(torrents_before[0].get("name") or "unknown")[:80]

        try:
            del_resp = await qbit_client.post(
                f"{base_url}/api/v2/torrents/delete",
                data={"hashes": torrent_hash, "deleteFiles": "true"},
            )
        except httpx.HTTPError:
            logger.exception("qBittorrent HTTP error on delete")
            metrics.tool_calls_total.labels(tool="delete_torrent", status="http_error").inc()
            return text_result("qBittorrent unreachable — try again in a moment.", is_error=True)

        if del_resp.status_code != 200:
            metrics.tool_calls_total.labels(tool="delete_torrent", status="http_error").inc()
            return text_result(
                f"qBittorrent refused the delete (status {del_resp.status_code}).",
                is_error=True,
            )

        # qBit returns 200 even for an unknown hash, so verify by fetching again.
        try:
            torrents_after, _ = await fetch_torrents(qbit_client, base_url, hashes=torrent_hash)
        except (httpx.HTTPError, ValueError):
            # If verification fails, treat as success — we already issued the DELETE.
            # Logging captures the inconsistency.
            logger.warning("delete verification failed; assuming success", exc_info=True)
            torrents_after = []

        if torrents_after:
            metrics.tool_calls_total.labels(tool="delete_torrent", status="not_deleted").inc()
            return text_result(
                "qBittorrent accepted the request but the torrent is still listed. Try again.",
                is_error=True,
            )

        # Strip newlines so a hostile name can't inject fake log lines.
        log_name = torrent_name.replace("\n", " ").replace("\r", " ")
        logger.info(
            "destructive_action_executed: tool=delete_torrent admin=%s hash=%s name=%s",
            caller_id,
            torrent_hash,
            log_name,
        )
        metrics.tool_calls_total.labels(tool="delete_torrent", status="success").inc()
        return text_result(f"Deleted '{torrent_name}' and its files.", is_error=False)

    return [delete_torrent]
