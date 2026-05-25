"""Destructive Radarr tools — invoked behind the can_use_tool confirmation gate."""

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
from cleanrr.tools._user_request import find_user_request, render_lookup_error

logger = logging.getLogger(__name__)


def build_tools(
    radarr_client: httpx.AsyncClient,
    overseerr_client: httpx.AsyncClient,
    identity: Identity,
    settings: Settings,
) -> list[SdkMcpTool]:
    """Factory for destructive Radarr tools.

    Owner-scoped via Overseerr: find_user_request resolves title → caller's
    request → tmdbId, which we then look up in Radarr to get the internal
    movieId for the MoviesSearch command.
    """

    @tool(
        "force_research_movie",
        "Re-trigger a Radarr search for one of the user's own requested movies. "
        "Idempotent (kicks off another search). The user confirms in chat first. "
        "Use when a movie request has been sitting with no progress and the user "
        "wants to nudge it. Pass the title as the user said it.",
        {"title": str},
    )
    async def force_research_movie(args: dict[str, Any]) -> dict[str, Any]:
        title_input = args.get("title", "").strip()

        if settings.radarr_url is None or settings.radarr_api_key is None:
            cleanrr.metrics.tool_calls_total.labels(
                tool="force_research_movie", status="radarr_not_configured"
            ).inc()
            return text_result(
                "Radarr isn't configured yet — ask the admin to set RADARR_URL and RADARR_API_KEY.",
                is_error=True,
            )

        lookup = await find_user_request(overseerr_client, identity, settings, title_input)
        error_response = render_lookup_error(lookup, title_input)
        if error_response is not None:
            cleanrr.metrics.tool_calls_total.labels(
                tool="force_research_movie", status=lookup.status
            ).inc()
            return error_response

        if lookup.request is None:
            cleanrr.metrics.tool_calls_total.labels(
                tool="force_research_movie", status="http_error"
            ).inc()
            return text_result(
                "An error occurred while looking up your request — try again in a moment.",
                is_error=True,
            )

        media = lookup.request.get("media", {})
        tmdb_id = media.get("tmdbId")
        if tmdb_id is None or not isinstance(tmdb_id, int):
            cleanrr.metrics.tool_calls_total.labels(
                tool="force_research_movie", status="not_a_movie"
            ).inc()
            return text_result(
                "That looks like a TV show — try force_research_show instead.",
                is_error=False,
            )

        base_url = str(settings.radarr_url).rstrip("/")
        try:
            movie_resp = await radarr_client.get(
                f"{base_url}/api/v3/movie", params={"tmdbId": tmdb_id}
            )
        except httpx.HTTPError:
            logger.exception("Radarr HTTP error looking up movie")
            cleanrr.metrics.tool_calls_total.labels(
                tool="force_research_movie", status="http_error"
            ).inc()
            return text_result("Couldn't reach Radarr — try again in a moment.", is_error=True)

        if movie_resp.status_code != 200:
            cleanrr.metrics.tool_calls_total.labels(
                tool="force_research_movie", status="http_error"
            ).inc()
            return text_result("Couldn't reach Radarr — try again in a moment.", is_error=True)

        try:
            movie_data = movie_resp.json()
        except ValueError:
            cleanrr.metrics.tool_calls_total.labels(
                tool="force_research_movie", status="parse_error"
            ).inc()
            return text_result(
                "Unexpected response format from Radarr — try again later.", is_error=True
            )

        if not movie_data or not isinstance(movie_data, list):
            cleanrr.metrics.tool_calls_total.labels(
                tool="force_research_movie", status="not_in_radarr"
            ).inc()
            return text_result(
                "Overseerr has the request but Radarr hasn't picked it up yet. "
                "Re-search won't help until Radarr sees the movie.",
                is_error=False,
            )

        first = movie_data[0]
        if not isinstance(first, dict):
            cleanrr.metrics.tool_calls_total.labels(
                tool="force_research_movie", status="parse_error"
            ).inc()
            return text_result(
                "Unexpected response format from Radarr — try again later.", is_error=True
            )

        movie_id = first.get("id")
        title = str(first.get("title") or "Unknown")[:80]
        if not isinstance(movie_id, int):
            cleanrr.metrics.tool_calls_total.labels(
                tool="force_research_movie", status="parse_error"
            ).inc()
            return text_result(
                "Unexpected response format from Radarr — try again later.", is_error=True
            )

        try:
            cmd_resp = await radarr_client.post(
                f"{base_url}/api/v3/command",
                json={"name": "MoviesSearch", "movieIds": [movie_id]},
            )
        except httpx.HTTPError:
            logger.exception("Radarr HTTP error issuing MoviesSearch")
            cleanrr.metrics.tool_calls_total.labels(
                tool="force_research_movie", status="http_error"
            ).inc()
            return text_result("Couldn't reach Radarr — try again in a moment.", is_error=True)

        if cmd_resp.status_code not in (200, 201, 202):
            cleanrr.metrics.tool_calls_total.labels(
                tool="force_research_movie", status="http_error"
            ).inc()
            return text_result(
                f"Radarr refused the search command (status {cmd_resp.status_code}).",
                is_error=True,
            )

        # find_user_request already validated the contextvar is set; re-fetch
        # here so the audit log carries the caller alongside Radarr's movie_id.
        caller_id = current_telegram_user_id.get(None)
        log_title = title.replace("\n", " ").replace("\r", " ")
        logger.info(
            "destructive_action_executed: tool=force_research_movie user=%s movie_id=%d title=%s",
            caller_id,
            movie_id,
            log_title,
        )
        cleanrr.metrics.tool_calls_total.labels(tool="force_research_movie", status="success").inc()
        return text_result(f"Triggered re-search for '{title}'.", is_error=False)

    return [force_research_movie]
