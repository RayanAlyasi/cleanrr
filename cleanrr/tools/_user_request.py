from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from cleanrr.config import Settings
from cleanrr.identity import Identity
from cleanrr.tools._context import current_telegram_user_id
from cleanrr.tools._results import text_result

logger = logging.getLogger(__name__)

_REQUEST_FETCH_LIMIT = 50
_FUZZY_MATCH_CUTOFF = 0.4
_YEAR_PATTERN = re.compile(r"\s*\(?\b(19|20)\d{2}\b\)?\s*$")


@dataclass
class UserRequestLookup:
    status: Literal[
        "ok",
        "not_configured",
        "context_missing",
        "unlinked_user",
        "empty_input",
        "user_not_found",
        "http_error",
        "parse_error",
        "no_match",
        "multi_match",
    ]
    request: dict[str, Any] | None = None
    candidates: list[dict[str, Any]] | None = field(default=None)


async def _resolve_user_id(
    client: httpx.AsyncClient, base_url: str, username: str
) -> tuple[int | None, str]:
    user_search = await client.get(
        f"{base_url}/api/v1/user",
        params={"q": username, "take": 1},
    )
    if user_search.status_code == 404:
        return None, "user_not_found"
    if user_search.status_code != 200:
        return None, "http_error"

    try:
        user_data = user_search.json()
        users = user_data.get("results", [])
    except ValueError:
        return None, "parse_error"

    if not users:
        return None, "user_not_found"

    try:
        return users[0]["id"], "ok"
    except (KeyError, TypeError):
        return None, "parse_error"


async def find_user_request(
    overseerr_client: httpx.AsyncClient | None,
    identity: Identity,
    settings: Settings,
    title: str,
) -> UserRequestLookup:
    """Cross-reference telegram user → Overseerr request matching title.

    Caller must increment its own tool_calls_total metric based on returned status.
    """
    if (
        overseerr_client is None
        or settings.overseerr_url is None
        or settings.overseerr_api_key is None
    ):
        return UserRequestLookup(status="not_configured")

    try:
        telegram_user_id = current_telegram_user_id.get()
    except LookupError:
        logger.exception("ContextVar not set in tool")
        return UserRequestLookup(status="context_missing")

    overseerr_username = await identity.get_link(telegram_user_id)
    if overseerr_username is None:
        return UserRequestLookup(status="unlinked_user")

    title_input = title.strip()
    if not title_input:
        return UserRequestLookup(status="empty_input")

    base_url = str(settings.overseerr_url).rstrip("/")
    user_id, resolve_status = await _resolve_user_id(overseerr_client, base_url, overseerr_username)
    if user_id is None:
        return UserRequestLookup(status=resolve_status)  # type: ignore[arg-type]

    requests_resp = await overseerr_client.get(
        f"{base_url}/api/v1/user/{user_id}/requests",
        params={"take": _REQUEST_FETCH_LIMIT},
    )
    if requests_resp.status_code != 200:
        return UserRequestLookup(status="http_error")

    try:
        requests_data = requests_resp.json()
        requests_list = requests_data.get("results", [])
    except ValueError:
        return UserRequestLookup(status="parse_error")

    title_to_request: dict[str, dict[str, Any]] = {}
    for req in requests_list:
        media = req.get("media", {})
        media_title = media.get("title") or media.get("name")
        if media_title:
            title_to_request[media_title] = req

    query = _YEAR_PATTERN.sub("", title_input).lower()
    candidates_map = {t.lower(): t for t in title_to_request}
    matches = difflib.get_close_matches(query, candidates_map, n=3, cutoff=_FUZZY_MATCH_CUTOFF)

    if not matches:
        return UserRequestLookup(status="no_match")

    if len(matches) == 1:
        original_title = candidates_map[matches[0]]
        return UserRequestLookup(status="ok", request=title_to_request[original_title])

    candidate_list = []
    for match_key in matches:
        original_title = candidates_map[match_key]
        candidate_list.append(title_to_request[original_title])

    return UserRequestLookup(status="multi_match", candidates=candidate_list)


def render_lookup_error(lookup: UserRequestLookup, title_input: str) -> dict[str, Any] | None:
    if lookup.status == "ok":
        return None
    if lookup.status == "not_configured":
        return text_result(
            "Overseerr isn't configured yet — ask the admin to set "
            "OVERSEERR_URL and OVERSEERR_API_KEY.",
            is_error=True,
        )
    if lookup.status == "context_missing":
        return text_result("Internal error — couldn't identify caller.", is_error=True)
    if lookup.status == "unlinked_user":
        return text_result(
            "You haven't linked your Overseerr account yet. Send /link <code> "
            "first (ask the admin for a code).",
            is_error=False,
        )
    if lookup.status == "empty_input":
        return text_result("Tell me which title you're asking about.", is_error=False)
    if lookup.status == "user_not_found":
        return text_result(
            "Couldn't find your Overseerr account — admin may need to re-issue the link.",
            is_error=False,
        )
    if lookup.status == "parse_error":
        return text_result(
            "Unexpected response format from Overseerr — try again later.",
            is_error=True,
        )
    if lookup.status == "http_error":
        return text_result(
            "Couldn't reach Overseerr — try again in a moment.",
            is_error=True,
        )
    if lookup.status == "no_match":
        return text_result(
            f"I couldn't find a request matching '{title_input[:50]}'. "
            "Try /list to see all your requests.",
            is_error=False,
        )
    if lookup.status == "multi_match":
        if lookup.candidates is None:
            return text_result("An error occurred — try again later.", is_error=True)
        lines = [f"Found {len(lookup.candidates)} possible matches — which one?"]
        for req in lookup.candidates:
            media = req.get("media", {})
            title = media.get("title") or media.get("name")
            year = media.get("releaseYear")
            if year:
                lines.append(f"- {title} ({year})")
            else:
                lines.append(f"- {title}")
        return text_result("\n".join(lines), is_error=False)
    return None
