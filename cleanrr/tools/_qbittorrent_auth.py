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


def normalize_torrent_hash(value: object) -> str | None:
    """Strip/lowercase and validate a torrent hash (40-char SHA-1 hex).

    Returns the normalized hash, or None if invalid. Shared by the delete
    tool and its confirmation-prompt formatter so they can never disagree
    on what counts as a valid hash.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    if len(candidate) != 40 or not all(c in "0123456789abcdef" for c in candidate):
        return None
    return candidate


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

    # Login response shape varies by qBittorrent WebUI API version: older
    # releases return 200 with body "Ok." ("Fails." on bad credentials, also
    # 200); confirmed against a live 5.2.0 instance, newer releases return 204
    # with an empty body and set the session cookie, or 401 with no cookie on
    # bad credentials. Accept either success shape.
    ok = resp.status_code == 204 or (resp.status_code == 200 and resp.text.strip() == "Ok.")
    if not ok:
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
