"""Destructive Sonarr tools — invoked behind the can_use_tool confirmation gate."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from claude_agent_sdk import SdkMcpTool, tool

import cleanrr.metrics as metrics
from cleanrr.config import Settings
from cleanrr.identity import Identity
from cleanrr.tools._context import current_telegram_user_id
from cleanrr.tools._results import text_result
from cleanrr.tools._user_request import find_user_request, render_lookup_error

logger = logging.getLogger(__name__)


def build_tools(
    sonarr_client: httpx.AsyncClient,
    overseerr_client: httpx.AsyncClient,
    identity: Identity,
    settings: Settings,
) -> list[SdkMcpTool]:
    """Factory for destructive Sonarr tools.

    Owner-scoped via Overseerr: find_user_request resolves title → caller's
    request → tvdbId, which we then look up in Sonarr to get the internal
    seriesId for the SeriesSearch command (whole-series scope, not per-episode).
    """

    @tool(
        "force_research_show",
        "Re-trigger a Sonarr search for one of the user's own requested TV shows "
        "at the series level (searches all monitored episodes). Idempotent. The "
        "user confirms in chat first. Pass the title as the user said it.",
        {"title": str},
    )
    async def force_research_show(args: dict[str, Any]) -> dict[str, Any]:
        title_input = args.get("title", "").strip()

        if settings.sonarr_url is None or settings.sonarr_api_key is None:
            metrics.tool_calls_total.labels(
                tool="force_research_show", status="sonarr_not_configured"
            ).inc()
            return text_result(
                "Sonarr isn't configured yet — ask the admin to set SONARR_URL and SONARR_API_KEY.",
                is_error=True,
            )

        lookup = await find_user_request(overseerr_client, identity, settings, title_input)
        error_response = render_lookup_error(lookup, title_input)
        if error_response is not None:
            metrics.tool_calls_total.labels(tool="force_research_show", status=lookup.status).inc()
            return error_response

        if lookup.request is None:
            metrics.tool_calls_total.labels(tool="force_research_show", status="http_error").inc()
            return text_result(
                "An error occurred while looking up your request — try again in a moment.",
                is_error=True,
            )

        media = lookup.request.get("media", {})
        tvdb_id = media.get("tvdbId")
        if not tvdb_id:
            metrics.tool_calls_total.labels(tool="force_research_show", status="not_a_show").inc()
            return text_result(
                "That looks like a movie — try force_research_movie instead.",
                is_error=False,
            )

        base_url = str(settings.sonarr_url).rstrip("/")
        try:
            series_resp = await sonarr_client.get(
                f"{base_url}/api/v3/series", params={"tvdbId": tvdb_id}
            )
        except httpx.HTTPError:
            logger.exception("Sonarr HTTP error looking up series")
            metrics.tool_calls_total.labels(tool="force_research_show", status="http_error").inc()
            return text_result("Couldn't reach Sonarr — try again in a moment.", is_error=True)

        if series_resp.status_code != 200:
            metrics.tool_calls_total.labels(tool="force_research_show", status="http_error").inc()
            return text_result("Couldn't reach Sonarr — try again in a moment.", is_error=True)

        try:
            series_data = series_resp.json()
        except ValueError:
            metrics.tool_calls_total.labels(tool="force_research_show", status="parse_error").inc()
            return text_result(
                "Unexpected response format from Sonarr — try again later.", is_error=True
            )

        if not series_data or not isinstance(series_data, list):
            metrics.tool_calls_total.labels(
                tool="force_research_show", status="not_in_sonarr"
            ).inc()
            return text_result(
                "Overseerr has the request but Sonarr hasn't picked it up yet. "
                "Re-search won't help until Sonarr sees the show.",
                is_error=False,
            )

        first = series_data[0]
        if not isinstance(first, dict):
            metrics.tool_calls_total.labels(tool="force_research_show", status="parse_error").inc()
            return text_result(
                "Unexpected response format from Sonarr — try again later.", is_error=True
            )

        series_id = first.get("id")
        title = str(first.get("title") or "Unknown")[:80]
        if not isinstance(series_id, int):
            metrics.tool_calls_total.labels(tool="force_research_show", status="parse_error").inc()
            return text_result(
                "Unexpected response format from Sonarr — try again later.", is_error=True
            )

        try:
            cmd_resp = await sonarr_client.post(
                f"{base_url}/api/v3/command",
                json={"name": "SeriesSearch", "seriesId": series_id},
            )
        except httpx.HTTPError:
            logger.exception("Sonarr HTTP error issuing SeriesSearch")
            metrics.tool_calls_total.labels(tool="force_research_show", status="http_error").inc()
            return text_result("Couldn't reach Sonarr — try again in a moment.", is_error=True)

        if cmd_resp.status_code not in (200, 201, 202):
            metrics.tool_calls_total.labels(tool="force_research_show", status="http_error").inc()
            return text_result(
                f"Sonarr refused the search command (status {cmd_resp.status_code}).",
                is_error=True,
            )

        # find_user_request already validated the contextvar is set; re-fetch
        # here so the audit log carries the caller alongside Sonarr's series_id.
        caller_id = current_telegram_user_id.get(None)
        log_title = title.replace("\n", " ").replace("\r", " ")
        logger.info(
            "destructive_action_executed: tool=force_research_show user=%s series_id=%d title=%s",
            caller_id,
            series_id,
            log_title,
        )
        metrics.tool_calls_total.labels(tool="force_research_show", status="success").inc()
        return text_result(f"Triggered re-search for '{title}'.", is_error=False)

    return [force_research_show]
