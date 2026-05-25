"""Destructive Overseerr tools — invoked behind the can_use_tool confirmation gate."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from claude_agent_sdk import SdkMcpTool, tool

import cleanrr.metrics
from cleanrr.config import Settings
from cleanrr.identity import Identity
from cleanrr.tools._context import current_telegram_user_id
from cleanrr.tools._results import text_result
from cleanrr.tools._user_request import _resolve_user_id

logger = logging.getLogger(__name__)


def build_tools(
    client: httpx.AsyncClient, identity: Identity, settings: Settings
) -> list[SdkMcpTool]:
    """Factory for destructive Overseerr tools.

    These tools assume the SDK's can_use_tool callback (in cleanrr.permissions)
    has already obtained a user confirmation. The tool layer still enforces
    per-user ownership as a defense-in-depth check — Overseerr's DELETE endpoint
    accepts any authenticated admin-API call.
    """

    @tool(
        "remove_my_request",
        "Cancel one of the user's own Overseerr requests by ID. Destructive — the "
        "user is asked to confirm in chat before this actually runs. Use only after "
        "find_my_request has identified the right request ID; never guess.",
        {"request_id": int},
    )
    async def remove_my_request(args: dict[str, Any]) -> dict[str, Any]:
        if settings.overseerr_url is None or settings.overseerr_api_key is None:
            cleanrr.metrics.tool_calls_total.labels(
                tool="remove_my_request", status="not_configured"
            ).inc()
            return text_result(
                "Overseerr isn't configured yet — ask the admin to set "
                "OVERSEERR_URL and OVERSEERR_API_KEY.",
                is_error=True,
            )

        request_id = args.get("request_id")
        if not isinstance(request_id, int) or request_id <= 0:
            cleanrr.metrics.tool_calls_total.labels(
                tool="remove_my_request", status="bad_args"
            ).inc()
            return text_result("Bad request id.", is_error=True)

        try:
            telegram_user_id = current_telegram_user_id.get()
        except LookupError:
            logger.exception("ContextVar not set in remove_my_request")
            cleanrr.metrics.tool_calls_total.labels(
                tool="remove_my_request", status="context_missing"
            ).inc()
            return text_result("Internal error — couldn't identify caller.", is_error=True)

        overseerr_username = await identity.get_link(telegram_user_id)
        if overseerr_username is None:
            cleanrr.metrics.tool_calls_total.labels(
                tool="remove_my_request", status="unlinked_user"
            ).inc()
            return text_result(
                "You haven't linked your Overseerr account yet. Send /link <code> "
                "first (ask the admin for a code).",
                is_error=False,
            )

        base_url = str(settings.overseerr_url).rstrip("/")
        caller_user_id, resolve_status = await _resolve_user_id(
            client, base_url, overseerr_username
        )
        if caller_user_id is None:
            cleanrr.metrics.tool_calls_total.labels(
                tool="remove_my_request", status=resolve_status
            ).inc()
            if resolve_status == "user_not_found":
                return text_result(
                    "Couldn't find your Overseerr account — admin may need to re-issue the link.",
                    is_error=False,
                )
            return text_result(
                "Couldn't reach Overseerr — try again in a moment.",
                is_error=True,
            )

        try:
            get_resp = await client.get(f"{base_url}/api/v1/request/{request_id}")
        except httpx.HTTPError:
            logger.exception("HTTP error fetching request %d", request_id)
            cleanrr.metrics.tool_calls_total.labels(
                tool="remove_my_request", status="http_error"
            ).inc()
            return text_result(
                "Couldn't reach Overseerr — try again in a moment.",
                is_error=True,
            )

        if get_resp.status_code == 404:
            cleanrr.metrics.tool_calls_total.labels(
                tool="remove_my_request", status="already_removed"
            ).inc()
            return text_result("Request already removed.", is_error=False)
        if get_resp.status_code != 200:
            cleanrr.metrics.tool_calls_total.labels(
                tool="remove_my_request", status="http_error"
            ).inc()
            return text_result(
                f"Couldn't fetch request (status {get_resp.status_code}).",
                is_error=True,
            )

        try:
            request_data = get_resp.json()
        except ValueError:
            cleanrr.metrics.tool_calls_total.labels(
                tool="remove_my_request", status="parse_error"
            ).inc()
            return text_result(
                "Unexpected response format from Overseerr — try again later.",
                is_error=True,
            )

        requested_by = request_data.get("requestedBy") or {}
        owner_id = requested_by.get("id")
        if owner_id != caller_user_id:
            # Ownership failure is a pre-confirmation guard, not a confirmation outcome,
            # so it stays out of destructive_actions_total (which is locked to the
            # Outcome literal). tool_calls_total carries the unauthorized signal.
            cleanrr.metrics.tool_calls_total.labels(
                tool="remove_my_request", status="unauthorized"
            ).inc()
            logger.warning(
                "remove_my_request ownership mismatch: caller=%s request_owner=%s request_id=%d",
                caller_user_id,
                owner_id,
                request_id,
            )
            return text_result("Not your request.", is_error=True)

        try:
            del_resp = await client.delete(f"{base_url}/api/v1/request/{request_id}")
        except httpx.HTTPError:
            logger.exception("HTTP error deleting request %d", request_id)
            cleanrr.metrics.tool_calls_total.labels(
                tool="remove_my_request", status="http_error"
            ).inc()
            return text_result(
                "Couldn't reach Overseerr — try again in a moment.",
                is_error=True,
            )

        if del_resp.status_code in (200, 204):
            media = request_data.get("media") or {}
            title = str(media.get("title") or media.get("name") or "Unknown")[:80]
            # Strip newlines so a hostile title can't inject fake log lines.
            log_title = title.replace("\n", " ").replace("\r", " ")
            logger.info(
                "destructive_action_executed: tool=remove_my_request "
                "user=%s request_id=%d title=%s",
                telegram_user_id,
                request_id,
                log_title,
            )
            cleanrr.metrics.tool_calls_total.labels(
                tool="remove_my_request", status="success"
            ).inc()
            return text_result(f"Cancelled '{title}'.", is_error=False)
        if del_resp.status_code == 404:
            cleanrr.metrics.tool_calls_total.labels(
                tool="remove_my_request", status="already_removed"
            ).inc()
            return text_result("Request already removed.", is_error=False)

        cleanrr.metrics.tool_calls_total.labels(tool="remove_my_request", status="http_error").inc()
        return text_result(
            f"Overseerr refused the delete (status {del_resp.status_code}).",
            is_error=True,
        )

    return [remove_my_request]
