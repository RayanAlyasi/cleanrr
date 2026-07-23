"""Confirmation-prompt text builders — one per destructive tool."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from cleanrr.config import Settings

_FORMATTER_TIMEOUT_SECONDS = 1.5

ConfirmationFormatter = Callable[[dict[str, Any]], Awaitable[str]]


def _build_remove_my_request_formatter(
    overseerr_client: httpx.AsyncClient | None,
    settings: Settings,
) -> ConfirmationFormatter:
    """Formatter that enriches the confirmation prompt with the request title and status."""

    async def formatter(tool_args: dict[str, Any]) -> str:
        request_id = tool_args.get("request_id")
        fallback = f"Cancel Overseerr request #{request_id}?"
        if overseerr_client is None or settings.overseerr_url is None or request_id is None:
            return fallback
        base_url = str(settings.overseerr_url).rstrip("/")
        try:
            resp = await asyncio.wait_for(
                overseerr_client.get(f"{base_url}/api/v1/request/{request_id}"),
                timeout=_FORMATTER_TIMEOUT_SECONDS,
            )
        except (TimeoutError, httpx.HTTPError):
            return fallback
        if resp.status_code != 200:
            return fallback
        try:
            data = resp.json()
        except ValueError:
            return fallback
        media = data.get("media") or {}
        # Overseerr title/name fields come from external metadata and may be
        # arbitrarily long; cap so a hostile entry can't blow past Telegram's
        # 4096-char message limit.
        title = str(media.get("title") or media.get("name") or "Unknown")[:80]
        media_type = str(media.get("mediaType") or "media")[:20]
        status_label = _request_status_label(data.get("status"))
        return (
            f"Cancel request: {title} ({media_type}, status: {status_label})? "
            "This removes the Overseerr request only — it does not delete "
            "already-downloaded media."
        )

    return formatter


_OVERSEERR_REQUEST_STATUS_LABELS = {
    1: "pending",
    2: "approved",
    3: "declined",
}


def _request_status_label(status: object) -> str:
    if isinstance(status, int):
        return _OVERSEERR_REQUEST_STATUS_LABELS.get(status, f"status {status}")
    return "unknown"


def _format_bytes(size: object) -> str:
    if not isinstance(size, int | float) or size < 0:
        return "?"
    if size >= 1_073_741_824:
        return f"{size / 1_073_741_824:.1f} GB"
    if size >= 1_048_576:
        return f"{size / 1_048_576:.0f} MB"
    return f"{int(size)} B"


def _build_delete_torrent_formatter(
    qbit_client: httpx.AsyncClient | None,
    settings: Settings,
) -> ConfirmationFormatter:
    """Look up the torrent name + size to make the confirmation prompt meaningful."""

    async def formatter(tool_args: dict[str, Any]) -> str:
        raw = tool_args.get("torrent_hash")
        # Mirror the tool's own normalization (strip + lower) BEFORE validating,
        # so the prompt's "invalid" verdict matches what the tool will actually do.
        # Otherwise a hash with stray whitespace renders as "invalid" but then
        # passes the tool's check, and the confirmation prompt becomes a lie.
        torrent_hash = raw.strip() if isinstance(raw, str) else raw
        if (
            not isinstance(torrent_hash, str)
            or len(torrent_hash) != 40
            or not all(c in "0123456789abcdefABCDEF" for c in torrent_hash)
        ):
            shown = (
                torrent_hash[:40] + "..."
                if isinstance(torrent_hash, str) and torrent_hash
                else "<missing>"
            )
            return f"Delete torrent (invalid hash: {shown}) AND its files? Tool will refuse."
        normalized = torrent_hash.lower()
        fallback = f"Delete torrent {normalized} AND its files from disk? This cannot be undone."
        if qbit_client is None or settings.qbittorrent_url is None:
            return fallback
        base_url = str(settings.qbittorrent_url).rstrip("/")
        # The qBit session may not be live here (the read tools log in lazily).
        # If unauthenticated we just fall back rather than try to log in from
        # the formatter path.
        try:
            resp = await asyncio.wait_for(
                qbit_client.get(f"{base_url}/api/v2/torrents/info", params={"hashes": normalized}),
                timeout=_FORMATTER_TIMEOUT_SECONDS,
            )
        except (TimeoutError, httpx.HTTPError):
            return fallback
        if resp.status_code != 200:
            return fallback
        try:
            data = resp.json()
        except ValueError:
            return fallback
        if not isinstance(data, list) or not data:
            return fallback
        entry = data[0]
        if not isinstance(entry, dict):
            return fallback
        name = str(entry.get("name") or "unknown")[:80]
        size = _format_bytes(entry.get("size"))
        return (
            f"Delete torrent '{name}' ({size}) AND its downloaded files from disk? "
            "This cannot be undone."
        )

    return formatter


def _build_force_research_movie_formatter() -> ConfirmationFormatter:
    async def formatter(tool_args: dict[str, Any]) -> str:
        title = str(tool_args.get("title") or "")[:80] or "your movie"
        return f"Re-search Radarr for '{title}'?"

    return formatter


def _build_force_research_show_formatter() -> ConfirmationFormatter:
    async def formatter(tool_args: dict[str, Any]) -> str:
        title = str(tool_args.get("title") or "")[:80] or "your show"
        return f"Re-search Sonarr for '{title}' (whole series)?"

    return formatter


def build_confirmation_formatters(
    overseerr_client: httpx.AsyncClient | None,
    qbit_client: httpx.AsyncClient | None,
    settings: Settings,
) -> dict[str, ConfirmationFormatter]:
    return {
        "remove_my_request": _build_remove_my_request_formatter(overseerr_client, settings),
        "delete_torrent": _build_delete_torrent_formatter(qbit_client, settings),
        "force_research_movie": _build_force_research_movie_formatter(),
        "force_research_show": _build_force_research_show_formatter(),
    }
