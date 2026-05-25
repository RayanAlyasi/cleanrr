"""Shared qBittorrent session-auth helpers.

qBit's WebUI uses session cookies — login once, the client carries the cookie
on subsequent calls, and a 403 means the session expired and we must re-auth.
Both the read-only and destructive qBit tools share this dance.
"""

from __future__ import annotations

from typing import Any

import httpx

from cleanrr.config import Settings


class QbitAuthError(Exception):
    pass


async def login(client: httpx.AsyncClient, base_url: str, settings: Settings) -> None:
    password = settings.qbittorrent_password
    if password is None:
        raise QbitAuthError("no password configured")
    try:
        resp = await client.post(
            f"{base_url}/api/v2/auth/login",
            data={
                "username": settings.qbittorrent_username,
                "password": password.get_secret_value(),
            },
        )
    except httpx.HTTPError as exc:
        raise QbitAuthError(str(exc)) from exc

    if resp.status_code != 200 or resp.text.strip() != "Ok.":
        raise QbitAuthError(f"login rejected (status={resp.status_code})")


async def fetch_torrents(
    client: httpx.AsyncClient, base_url: str, hashes: str | None = None
) -> tuple[list[dict[str, Any]], bool]:
    """Return (torrent_list, needs_reauth).

    Pass ``hashes`` (pipe-separated) to filter to specific torrents; omit to
    fetch all. ``needs_reauth`` is True when the server returned 403.
    """
    params: dict[str, str] = {}
    if hashes is not None:
        params["hashes"] = hashes
    resp = await client.get(f"{base_url}/api/v2/torrents/info", params=params)
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
