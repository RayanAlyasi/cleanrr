import difflib
import logging
import re
from typing import Any

import httpx
from claude_agent_sdk import SdkMcpTool, tool

import cleanrr.metrics
from cleanrr.config import Settings
from cleanrr.identity import Identity
from cleanrr.tools._context import current_telegram_user_id
from cleanrr.tools._results import text_result

logger = logging.getLogger(__name__)

_REQUEST_FETCH_LIMIT = 50
_FUZZY_MATCH_CUTOFF = 0.4
_YEAR_PATTERN = re.compile(r"\s*\(?\b(19|20)\d{2}\b\)?\s*$")


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


def _format_status_label(req_status: int | None, media_status: int | None) -> str:
    status_parts = []
    if req_status == 1:
        status_parts.append("pending")
    elif req_status == 2:
        status_parts.append("approved")
    elif req_status == 3:
        status_parts.append("declined")

    if media_status == 2:
        status_parts.append("pending download")
    elif media_status == 3:
        status_parts.append("processing")
    elif media_status == 4:
        status_parts.append("partially available")
    elif media_status == 5:
        status_parts.append("available")

    return ", ".join(status_parts) if status_parts else "unknown"


def build_tools(
    client: httpx.AsyncClient, identity: Identity, settings: Settings
) -> list[SdkMcpTool]:
    """Factory for Overseerr tools."""

    @tool(
        "list_my_requests",
        "List the Overseerr media requests made by the user who is currently chatting. "
        "Use this when the user asks 'where's my movie?', 'what did I request?', or any variation. "
        "Returns request titles and statuses.",
        {"status": str},
    )
    async def list_my_requests(_args: dict[str, Any]) -> dict[str, Any]:
        # 1. Check if Overseerr is configured
        if settings.overseerr_url is None or settings.overseerr_api_key is None:
            cleanrr.metrics.tool_calls_total.labels(
                tool="list_my_requests", status="not_configured"
            ).inc()
            return text_result(
                "Overseerr isn't configured yet — ask the admin to set "
                "OVERSEERR_URL and OVERSEERR_API_KEY.",
                is_error=True,
            )

        # 2. Get calling user's telegram ID
        try:
            telegram_user_id = current_telegram_user_id.get()
        except LookupError:
            logger.exception("ContextVar not set in tool")
            cleanrr.metrics.tool_calls_total.labels(
                tool="list_my_requests", status="context_missing"
            ).inc()
            return text_result("Internal error — couldn't identify caller.", is_error=True)

        # 3. Resolve telegram ID → Overseerr username
        overseerr_username = await identity.get_link(telegram_user_id)
        if overseerr_username is None:
            cleanrr.metrics.tool_calls_total.labels(
                tool="list_my_requests", status="unlinked_user"
            ).inc()
            return text_result(
                "You haven't linked your Overseerr account yet. Send /link <code> "
                "first (ask the admin for a code).",
                is_error=False,
            )

        try:
            base_url = str(settings.overseerr_url).rstrip("/")
            user_id, resolve_status = await _resolve_user_id(client, base_url, overseerr_username)
            if user_id is None:
                if resolve_status == "user_not_found":
                    cleanrr.metrics.tool_calls_total.labels(
                        tool="list_my_requests", status="user_not_found"
                    ).inc()
                    return text_result(
                        "Couldn't find your Overseerr account — "
                        "admin may need to re-issue the link.",
                        is_error=False,
                    )
                if resolve_status == "parse_error":
                    cleanrr.metrics.tool_calls_total.labels(
                        tool="list_my_requests", status="parse_error"
                    ).inc()
                    return text_result(
                        "Unexpected response format from Overseerr — try again later.",
                        is_error=True,
                    )
                cleanrr.metrics.tool_calls_total.labels(
                    tool="list_my_requests", status="http_error"
                ).inc()
                return text_result(
                    "Couldn't reach Overseerr — try again in a moment.",
                    is_error=True,
                )

            # 5. Fetch requests
            requests_resp = await client.get(
                f"{base_url}/api/v1/user/{user_id}/requests",
                params={"take": 20},
            )
            if requests_resp.status_code != 200:
                cleanrr.metrics.tool_calls_total.labels(
                    tool="list_my_requests", status="http_error"
                ).inc()
                return text_result(
                    "Couldn't fetch your requests — try again in a moment.",
                    is_error=True,
                )

            try:
                requests_data = requests_resp.json()
                requests_list = requests_data.get("results", [])
            except ValueError:
                cleanrr.metrics.tool_calls_total.labels(
                    tool="list_my_requests", status="parse_error"
                ).inc()
                return text_result(
                    "Unexpected response format from Overseerr — try again later.",
                    is_error=True,
                )

            if not requests_list:
                cleanrr.metrics.tool_calls_total.labels(
                    tool="list_my_requests", status="success"
                ).inc()
                return text_result(
                    "You haven't requested anything via Overseerr yet.",
                    is_error=False,
                )

            lines = [f"You have {len(requests_list)} Overseerr request(s):"]
            for req in requests_list:
                media = req.get("media", {})
                req_status = req.get("status")
                media_status = media.get("status")

                status_label = _format_status_label(req_status, media_status)

                title = media.get("title") or media.get("name") or "Unknown"
                year = media.get("releaseYear")
                if year:
                    lines.append(f"- {title} ({year}) — {status_label}")
                else:
                    lines.append(f"- {title} — {status_label}")

            cleanrr.metrics.tool_calls_total.labels(tool="list_my_requests", status="success").inc()
            return text_result("\n".join(lines), is_error=False)

        except Exception:
            logger.exception("Overseerr tool error")
            cleanrr.metrics.tool_calls_total.labels(
                tool="list_my_requests", status="http_error"
            ).inc()
            return text_result(
                "An error occurred while fetching your requests — try again in a moment.",
                is_error=True,
            )

    @tool(
        "find_my_request",
        "Find a specific Overseerr request by the user's media title — fuzzy-matched. "
        "Use this when the user asks about ONE title ('is X ready?', 'has Y downloaded?'). "
        "Use list_my_requests instead when the user wants their full list.",
        {"title": str},
    )
    async def find_my_request(args: dict[str, Any]) -> dict[str, Any]:
        title_input = args.get("title", "").strip()

        if settings.overseerr_url is None or settings.overseerr_api_key is None:
            cleanrr.metrics.tool_calls_total.labels(
                tool="find_my_request", status="not_configured"
            ).inc()
            return text_result(
                "Overseerr isn't configured yet — ask the admin to set "
                "OVERSEERR_URL and OVERSEERR_API_KEY.",
                is_error=True,
            )

        try:
            telegram_user_id = current_telegram_user_id.get()
        except LookupError:
            logger.exception("ContextVar not set in tool")
            cleanrr.metrics.tool_calls_total.labels(
                tool="find_my_request", status="context_missing"
            ).inc()
            return text_result("Internal error — couldn't identify caller.", is_error=True)

        overseerr_username = await identity.get_link(telegram_user_id)
        if overseerr_username is None:
            cleanrr.metrics.tool_calls_total.labels(
                tool="find_my_request", status="unlinked_user"
            ).inc()
            return text_result(
                "You haven't linked your Overseerr account yet. Send /link <code> "
                "first (ask the admin for a code).",
                is_error=False,
            )

        if not title_input:
            cleanrr.metrics.tool_calls_total.labels(
                tool="find_my_request", status="empty_input"
            ).inc()
            return text_result(
                "Tell me which title you're asking about.",
                is_error=False,
            )

        try:
            base_url = str(settings.overseerr_url).rstrip("/")
            user_id, resolve_status = await _resolve_user_id(client, base_url, overseerr_username)
            if user_id is None:
                if resolve_status == "user_not_found":
                    cleanrr.metrics.tool_calls_total.labels(
                        tool="find_my_request", status="user_not_found"
                    ).inc()
                    return text_result(
                        "Couldn't find your Overseerr account — "
                        "admin may need to re-issue the link.",
                        is_error=False,
                    )
                if resolve_status == "parse_error":
                    cleanrr.metrics.tool_calls_total.labels(
                        tool="find_my_request", status="parse_error"
                    ).inc()
                    return text_result(
                        "Unexpected response format from Overseerr — try again later.",
                        is_error=True,
                    )
                cleanrr.metrics.tool_calls_total.labels(
                    tool="find_my_request", status="http_error"
                ).inc()
                return text_result(
                    "Couldn't reach Overseerr — try again in a moment.",
                    is_error=True,
                )

            requests_resp = await client.get(
                f"{base_url}/api/v1/user/{user_id}/requests",
                params={"take": _REQUEST_FETCH_LIMIT},
            )
            if requests_resp.status_code != 200:
                cleanrr.metrics.tool_calls_total.labels(
                    tool="find_my_request", status="http_error"
                ).inc()
                return text_result(
                    "Couldn't reach Overseerr — try again in a moment.",
                    is_error=True,
                )

            try:
                requests_data = requests_resp.json()
                requests_list = requests_data.get("results", [])
            except ValueError:
                cleanrr.metrics.tool_calls_total.labels(
                    tool="find_my_request", status="parse_error"
                ).inc()
                return text_result(
                    "Unexpected response format from Overseerr — try again later.",
                    is_error=True,
                )

            # Build title → request mapping
            title_to_request = {}
            for req in requests_list:
                media = req.get("media", {})
                media_title = media.get("title") or media.get("name")
                if media_title:
                    title_to_request[media_title] = req

            # Fuzzy match
            query = _YEAR_PATTERN.sub("", title_input).lower()
            candidates = {t.lower(): t for t in title_to_request}
            matches = difflib.get_close_matches(query, candidates, n=3, cutoff=_FUZZY_MATCH_CUTOFF)

            if not matches:
                cleanrr.metrics.tool_calls_total.labels(
                    tool="find_my_request", status="no_match"
                ).inc()
                title_truncated = title_input[:50]
                return text_result(
                    f"I couldn't find a request matching '{title_truncated}'. "
                    "Try /list to see all your requests.",
                    is_error=False,
                )

            if len(matches) == 1:
                original_title = candidates[matches[0]]
                req = title_to_request[original_title]
                media = req.get("media", {})
                req_status = req.get("status")
                media_status = media.get("status")
                status_label = _format_status_label(req_status, media_status)

                year = media.get("releaseYear")
                if year:
                    result_text = f"Your request for {original_title} ({year}): {status_label}."
                else:
                    result_text = f"Your request for {original_title}: {status_label}."

                cleanrr.metrics.tool_calls_total.labels(
                    tool="find_my_request", status="success"
                ).inc()
                return text_result(result_text, is_error=False)

            # Multi-match
            disambiguation_lines = [f"Found {len(matches)} possible matches — which one?"]
            for match_key in matches:
                original_title = candidates[match_key]
                req = title_to_request[original_title]
                media = req.get("media", {})
                year = media.get("releaseYear")
                if year:
                    disambiguation_lines.append(f"- {original_title} ({year})")
                else:
                    disambiguation_lines.append(f"- {original_title}")

            cleanrr.metrics.tool_calls_total.labels(
                tool="find_my_request", status="multi_match"
            ).inc()
            return text_result("\n".join(disambiguation_lines), is_error=False)

        except Exception:
            logger.exception("find_my_request error")
            cleanrr.metrics.tool_calls_total.labels(
                tool="find_my_request", status="http_error"
            ).inc()
            return text_result(
                "An error occurred while fetching your requests — try again in a moment.",
                is_error=True,
            )

    return [list_my_requests, find_my_request]
