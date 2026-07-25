from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx
from claude_agent_sdk import SdkMcpTool, tool

import cleanrr.metrics as metrics
from cleanrr.config import Settings
from cleanrr.identity import Identity
from cleanrr.tools._context import current_telegram_user_id
from cleanrr.tools._results import text_result
from cleanrr.tools._status_label import _format_status_label
from cleanrr.tools._user_request import (
    _REQUEST_FETCH_LIMIT,
    _resolve_user_id,
    enrich_titles_with_names,
    find_user_request,
    render_lookup_error,
)

if TYPE_CHECKING:
    import telegram

logger = logging.getLogger(__name__)


def _user_id_error_response(tool_name: str, resolve_status: str) -> dict[str, Any]:
    if resolve_status == "user_not_found":
        metrics.tool_calls_total.labels(tool=tool_name, status="user_not_found").inc()
        return text_result(
            "Couldn't find your Overseerr account — admin may need to re-issue the link.",
            is_error=False,
        )
    if resolve_status == "parse_error":
        metrics.tool_calls_total.labels(tool=tool_name, status="parse_error").inc()
        return text_result(
            "Unexpected response format from Overseerr — try again later.",
            is_error=True,
        )
    metrics.tool_calls_total.labels(tool=tool_name, status="http_error").inc()
    return text_result(
        "Couldn't reach Overseerr — try again in a moment.",
        is_error=True,
    )


def build_tools(
    client: httpx.AsyncClient,
    identity: Identity,
    settings: Settings,
    telegram_bot: telegram.Bot | None = None,
) -> list[SdkMcpTool]:
    """Factory for Overseerr tools."""

    @tool(
        "list_my_requests",
        "List the Overseerr media requests made by the user who is currently chatting. "
        "Use this when the user asks 'where's my movie?', 'what did I request?', or any variation. "
        "Returns request titles and statuses.",
        {},
    )
    async def list_my_requests(_args: dict[str, Any]) -> dict[str, Any]:
        if settings.overseerr_url is None or settings.overseerr_api_key is None:
            metrics.tool_calls_total.labels(tool="list_my_requests", status="not_configured").inc()
            return text_result(
                "Overseerr isn't configured yet — ask the admin to set "
                "OVERSEERR_URL and OVERSEERR_API_KEY.",
                is_error=True,
            )

        try:
            telegram_user_id = current_telegram_user_id.get()
        except LookupError:
            logger.exception("ContextVar not set in tool")
            metrics.tool_calls_total.labels(tool="list_my_requests", status="context_missing").inc()
            return text_result("Internal error — couldn't identify caller.", is_error=True)

        overseerr_username = await identity.get_link(telegram_user_id)
        if overseerr_username is None:
            metrics.tool_calls_total.labels(tool="list_my_requests", status="unlinked_user").inc()
            return text_result(
                "You haven't linked your Overseerr account yet. Send /link <code> "
                "first (ask the admin for a code).",
                is_error=False,
            )

        try:
            base_url = str(settings.overseerr_url).rstrip("/")
            user_id, resolve_status = await _resolve_user_id(client, base_url, overseerr_username)
            if user_id is None:
                return _user_id_error_response("list_my_requests", resolve_status)

            requests_resp = await client.get(
                f"{base_url}/api/v1/user/{user_id}/requests",
                params={"take": _REQUEST_FETCH_LIMIT},
            )
            if requests_resp.status_code != 200:
                metrics.tool_calls_total.labels(tool="list_my_requests", status="http_error").inc()
                return text_result(
                    "Couldn't fetch your requests — try again in a moment.",
                    is_error=True,
                )

            try:
                requests_data = requests_resp.json()
            except ValueError:
                metrics.tool_calls_total.labels(tool="list_my_requests", status="parse_error").inc()
                return text_result(
                    "Unexpected response format from Overseerr — try again later.",
                    is_error=True,
                )
            if not isinstance(requests_data, dict):
                metrics.tool_calls_total.labels(tool="list_my_requests", status="parse_error").inc()
                return text_result(
                    "Unexpected response format from Overseerr — try again later.",
                    is_error=True,
                )
            requests_list = requests_data.get("results", [])
            if not isinstance(requests_list, list):
                metrics.tool_calls_total.labels(tool="list_my_requests", status="parse_error").inc()
                return text_result(
                    "Unexpected response format from Overseerr — try again later.",
                    is_error=True,
                )

            if not requests_list:
                metrics.tool_calls_total.labels(tool="list_my_requests", status="success").inc()
                return text_result(
                    "You haven't requested anything via Overseerr yet.",
                    is_error=False,
                )

            await enrich_titles_with_names(client, base_url, requests_list)

            # pageInfo.results is Overseerr's real total; len(requests_list) is
            # only this page's size and understates the count once truncated
            # by _REQUEST_FETCH_LIMIT.
            page_info = requests_data.get("pageInfo")
            total = page_info.get("results") if isinstance(page_info, dict) else None
            total = total if isinstance(total, int) else len(requests_list)
            header = f"You have {total} Overseerr request(s)"
            if total > len(requests_list):
                header += f" (showing the {len(requests_list)} most recent)"
            lines = [header + ":"]
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

            metrics.tool_calls_total.labels(tool="list_my_requests", status="success").inc()
            return text_result("\n".join(lines), is_error=False)

        except Exception:
            logger.exception("Overseerr tool error")
            metrics.tool_calls_total.labels(tool="list_my_requests", status="http_error").inc()
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

        lookup = await find_user_request(
            client, identity, settings, title_input, telegram_bot=telegram_bot
        )
        metric_status = "success" if lookup.status == "ok" else lookup.status
        metrics.tool_calls_total.labels(tool="find_my_request", status=metric_status).inc()

        error_response = render_lookup_error(lookup, title_input)
        if error_response is not None:
            return error_response

        if lookup.request is None:
            return text_result(
                "An error occurred while fetching your requests — try again in a moment.",
                is_error=True,
            )

        media = lookup.request.get("media", {})
        req_status = lookup.request.get("status")
        media_status = media.get("status")
        status_label = _format_status_label(req_status, media_status)

        title = media.get("title") or media.get("name")
        year = media.get("releaseYear")
        if year:
            result_text = f"Your request for {title} ({year}): {status_label}."
        else:
            result_text = f"Your request for {title}: {status_label}."

        return text_result(result_text, is_error=False)

    return [list_my_requests, find_my_request]
