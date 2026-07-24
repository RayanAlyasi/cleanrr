from __future__ import annotations

import asyncio
import difflib
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import httpx
from telegram.error import TelegramError

from cleanrr.config import Settings
from cleanrr.identity import Identity
from cleanrr.tools._context import current_telegram_user_id
from cleanrr.tools._results import text_result

if TYPE_CHECKING:
    import telegram

logger = logging.getLogger(__name__)

_REQUEST_FETCH_LIMIT = 50
_FUZZY_MATCH_CUTOFF = 0.6
_FUZZY_MATCH_LIMIT = 3
_YEAR_PATTERN = re.compile(r"\s*\(?\b(19|20)\d{2}\b\)?\s*$")
# Caps concurrent /movie or /tv detail calls per enrich_titles_with_names() batch —
# a self-hosted Overseerr shouldn't take 50 simultaneous requests for one lookup.
_TITLE_FETCH_CONCURRENCY = 8
# TMDB's public image CDN — Overseerr's posterPath is always a TMDB-relative
# path (confirmed against a live instance), not a full URL.
_TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"
# Tool calls run as detached tasks the SDK doesn't cancel when the outer
# Agent.respond() timeout fires (they aren't awaited inline by
# receive_response()), so an unbounded send_photo call here wouldn't be cut
# off by claude_timeout_seconds — it would just keep running against the
# shared subprocess after the caller has already timed out. Bound each send
# individually, same pattern as _FORMATTER_TIMEOUT_SECONDS.
_PHOTO_SEND_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class MediaDetails:
    title: str | None
    poster_path: str | None
    year: int | None


def _parse_year(date_str: object) -> int | None:
    if not isinstance(date_str, str) or len(date_str) < 4:
        return None
    try:
        return int(date_str[:4])
    except ValueError:
        return None


async def _fetch_media_details(
    client: httpx.AsyncClient, base_url: str, media_type: str, tmdb_id: int
) -> MediaDetails | None:
    """Look up a movie/show's display name, poster, and release year.

    Overseerr's request-list endpoints return only tmdbId/tvdbId — title,
    name, posterPath, and release date all come solely from the per-item
    /movie or /tv detail endpoint. Movies key title "title" and date
    "releaseDate"; TV shows key title "name" and date "firstAirDate" — there
    is no "releaseYear" field anywhere in Overseerr's schema, confirmed
    against a live instance and its OpenAPI spec. posterPath is a
    TMDB-relative path (e.g. "/gDzOcq0...jpg"); the caller builds a full URL
    via _TMDB_IMAGE_BASE.
    """
    endpoint = "movie" if media_type == "movie" else "tv"
    try:
        resp = await client.get(f"{base_url}/api/v1/{endpoint}/{tmdb_id}")
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    title = data.get("title") or data.get("name")
    poster_path = data.get("posterPath")
    date_str = data.get("releaseDate") if media_type == "movie" else data.get("firstAirDate")
    return MediaDetails(
        title=str(title) if title else None,
        poster_path=poster_path if isinstance(poster_path, str) and poster_path else None,
        year=_parse_year(date_str),
    )


async def _fetch_media_title(
    client: httpx.AsyncClient, base_url: str, media_type: str, tmdb_id: int
) -> str | None:
    details = await _fetch_media_details(client, base_url, media_type, tmdb_id)
    return details.title if details is not None else None


async def enrich_titles_with_names(
    client: httpx.AsyncClient, base_url: str, requests_list: list[Any]
) -> None:
    """Fill in each request's media title/name, posterPath, and releaseYear
    in place, fetched concurrently. releaseYear doesn't exist on Overseerr's
    media objects — it's synthesized here from releaseDate/firstAirDate so
    every caller displaying a year can keep reading media["releaseYear"].

    No-op for entries that aren't dicts, lack a usable mediaType/tmdbId, or
    fail to resolve — callers already fall back to "Unknown" for those.
    """
    semaphore = asyncio.Semaphore(_TITLE_FETCH_CONCURRENCY)

    async def _fill(req: dict[str, Any]) -> None:
        media = req.get("media")
        if not isinstance(media, dict):
            return
        media_type = media.get("mediaType")
        tmdb_id = media.get("tmdbId")
        if media_type not in ("movie", "tv") or not isinstance(tmdb_id, int):
            return
        async with semaphore:
            details = await _fetch_media_details(client, base_url, media_type, tmdb_id)
        if details is None:
            return
        if details.title:
            media["title" if media_type == "movie" else "name"] = details.title
        if details.poster_path:
            media["posterPath"] = details.poster_path
        if details.year:
            media["releaseYear"] = details.year

    await asyncio.gather(*(_fill(req) for req in requests_list if isinstance(req, dict)))


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
    # True only if at least one poster photo actually sent — render_lookup_error
    # must not claim "sent photos above" when none went out.
    posters_sent: bool = False


ResolveUserStatus = Literal["ok", "user_not_found", "http_error", "parse_error"]


async def _send_match_photos(
    telegram_bot: telegram.Bot, chat_id: int, candidates: list[dict[str, Any]]
) -> int:
    """Best-effort: send each candidate's poster with a numbered caption so
    the user can pick visually. Returns how many actually sent — candidates
    without a posterPath are skipped, and a failed send for one candidate
    doesn't block the others. The numbered text list stays the reliable
    fallback regardless of how many (if any) photos make it out.
    """
    sent = 0
    for i, req in enumerate(candidates, start=1):
        media = req.get("media", {})
        if not isinstance(media, dict):
            continue
        poster_path = media.get("posterPath")
        if not isinstance(poster_path, str) or not poster_path:
            continue
        title = str(media.get("title") or media.get("name") or "?")[:80]
        year = media.get("releaseYear")
        caption = f"{i}. {title} ({year})" if year else f"{i}. {title}"
        try:
            await asyncio.wait_for(
                telegram_bot.send_photo(
                    chat_id=chat_id, photo=f"{_TMDB_IMAGE_BASE}{poster_path}", caption=caption
                ),
                timeout=_PHOTO_SEND_TIMEOUT_SECONDS,
            )
            sent += 1
        except TimeoutError:
            logger.warning("timed out sending match poster for %r", title)
        except TelegramError:
            logger.warning("failed to send match poster for %r", title, exc_info=True)
    return sent


def _title_match_score(query: str, candidate: str) -> float:
    """Score how well a candidate title matches a user's (lowercased) query.

    Plain difflib.SequenceMatcher.ratio() can't be trusted alone: it penalizes
    length differences, so a short, correct partial query like "dune" against
    "dune part one" scores ~0.47 — almost identical to the ~0.48 a completely
    unrelated title like "meet the fockers" scores against "the flash" purely
    from sharing common short words. No single ratio cutoff separates those
    two cases. Substring containment resolves the short-query case
    deterministically instead of by approximate ratio.
    """
    if query == candidate:
        return 2.0
    if query in candidate or candidate in query:
        return 1.0 + difflib.SequenceMatcher(None, query, candidate).ratio()
    return difflib.SequenceMatcher(None, query, candidate).ratio()


def _fuzzy_match_titles(query: str, candidates: list[str]) -> list[str]:
    """Return up to _FUZZY_MATCH_LIMIT candidates, best match first."""
    scored = [
        (c, s) for c in candidates if (s := _title_match_score(query, c)) >= _FUZZY_MATCH_CUTOFF
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [c for c, _ in scored[:_FUZZY_MATCH_LIMIT]]


_USER_SEARCH_PAGE_SIZE = 50


async def _resolve_user_id(
    client: httpx.AsyncClient, base_url: str, username: str
) -> tuple[int | None, ResolveUserStatus]:
    user_search = await client.get(
        f"{base_url}/api/v1/user",
        params={"q": username, "take": _USER_SEARCH_PAGE_SIZE},
    )
    if user_search.status_code == 404:
        return None, "user_not_found"
    if user_search.status_code != 200:
        return None, "http_error"

    try:
        user_data = user_search.json()
    except ValueError:
        return None, "parse_error"
    if not isinstance(user_data, dict):
        return None, "parse_error"
    users = user_data.get("results", [])
    if not isinstance(users, list):
        return None, "parse_error"

    if not users:
        return None, "user_not_found"

    # The `q` filter is a Jellyseerr extension, not guaranteed on vanilla
    # Overseerr's /user endpoint — a non-filtering backend would return an
    # unrelated page of users with `q` silently ignored. Prefer an exact
    # (case-insensitive) username match over every field Overseerr/Jellyseerr
    # may key a login by; only fall back to position 0 when there's a single
    # candidate to begin with (the ambiguous, dangerous case is specifically
    # *multiple* non-matching candidates — picking blindly there risks
    # resolving to the wrong account).
    target = username.casefold()

    def _matches(user: object) -> bool:
        if not isinstance(user, dict):
            return False
        candidates = (
            user.get("username"),
            user.get("plexUsername"),
            user.get("jellyfinUsername"),
        )
        return any(isinstance(c, str) and c.casefold() == target for c in candidates)

    matched = next((u for u in users if _matches(u)), None)
    if matched is None:
        if len(users) != 1:
            return None, "user_not_found"
        if not isinstance(users[0], dict):
            return None, "parse_error"
        matched = users[0]

    try:
        return matched["id"], "ok"
    except (KeyError, TypeError):
        return None, "parse_error"


async def find_user_request(
    overseerr_client: httpx.AsyncClient | None,
    identity: Identity,
    settings: Settings,
    title: str,
    *,
    telegram_bot: telegram.Bot | None = None,
) -> UserRequestLookup:
    """Cross-reference telegram user → Overseerr request matching title.

    Caller must increment its own tool_calls_total metric based on returned status.
    When telegram_bot is given and the result is multi_match, best-effort
    sends poster photos for the candidates so the user can pick visually —
    see UserRequestLookup.posters_sent.
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
        return UserRequestLookup(status=resolve_status)

    requests_resp = await overseerr_client.get(
        f"{base_url}/api/v1/user/{user_id}/requests",
        params={"take": _REQUEST_FETCH_LIMIT},
    )
    if requests_resp.status_code != 200:
        return UserRequestLookup(status="http_error")

    try:
        requests_data = requests_resp.json()
    except ValueError:
        return UserRequestLookup(status="parse_error")
    if not isinstance(requests_data, dict):
        return UserRequestLookup(status="parse_error")
    requests_list = requests_data.get("results", [])
    if not isinstance(requests_list, list):
        return UserRequestLookup(status="parse_error")

    await enrich_titles_with_names(overseerr_client, base_url, requests_list)

    title_to_request: dict[str, dict[str, Any]] = {}
    for req in requests_list:
        if not isinstance(req, dict):
            continue
        media = req.get("media", {})
        if not isinstance(media, dict):
            continue
        media_title = media.get("title") or media.get("name")
        if media_title:
            title_to_request[media_title] = req

    # Strip a trailing "(YYYY)" year suffix so "Dune (2021)" matches "Dune" —
    # but not when the whole query IS a year ("1917", "2012" are real titles),
    # which would otherwise leave an empty string that ties every candidate.
    query = _YEAR_PATTERN.sub("", title_input).lower() or title_input.lower()
    candidates_map = {t.lower(): t for t in title_to_request}
    matches = _fuzzy_match_titles(query, list(candidates_map))

    if not matches:
        return UserRequestLookup(status="no_match")

    if len(matches) == 1:
        original_title = candidates_map[matches[0]]
        return UserRequestLookup(status="ok", request=title_to_request[original_title])

    candidate_list = []
    for match_key in matches:
        original_title = candidates_map[match_key]
        candidate_list.append(title_to_request[original_title])

    posters_sent = False
    if telegram_bot is not None:
        posters_sent = await _send_match_photos(telegram_bot, telegram_user_id, candidate_list) > 0

    return UserRequestLookup(
        status="multi_match", candidates=candidate_list, posters_sent=posters_sent
    )


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
            "Ask me to list your requests to see everything you've requested.",
            is_error=False,
        )
    if lookup.status == "multi_match":
        if lookup.candidates is None:
            return text_result("An error occurred — try again later.", is_error=True)
        lines = [f"Found {len(lookup.candidates)} possible matches — which one?"]
        for i, req in enumerate(lookup.candidates, start=1):
            media = req.get("media", {})
            # API-supplied title is untrusted; bound it so a hostile/long value
            # can't blow past Telegram's 4096-char reply limit.
            title = str(media.get("title") or media.get("name") or "?")[:80]
            year = media.get("releaseYear")
            if year:
                lines.append(f"{i}. {title} ({year})")
            else:
                lines.append(f"{i}. {title}")
        if lookup.posters_sent:
            lines.append(
                "(Sent poster photos above, numbered to match this list — "
                "ask the user to reply with the number or title they mean.)"
            )
        return text_result("\n".join(lines), is_error=False)
    return None
