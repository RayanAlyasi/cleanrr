import logging
from typing import Any

import httpx
from claude_agent_sdk import SdkMcpTool, tool

import cleanrr.metrics
from cleanrr.config import Settings
from cleanrr.identity import Identity
from cleanrr.tools._results import text_result
from cleanrr.tools._user_request import find_user_request

logger = logging.getLogger(__name__)


def build_tools(
    radarr_client: httpx.AsyncClient,
    overseerr_client: httpx.AsyncClient,
    identity: Identity,
    settings: Settings,
) -> list[SdkMcpTool]:
    """Factory for Radarr tools."""

    @tool(
        "get_movie_status",
        "Look up the download status of a movie the user requested. "
        "Use when the user asks 'is X downloaded?', 'is my movie ready?'. "
        "Pass the movie title as the user said it.",
        {"title": str},
    )
    async def get_movie_status(args: dict[str, Any]) -> dict[str, Any]:
        title_input = args.get("title", "").strip()

        if settings.radarr_url is None or settings.radarr_api_key is None:
            cleanrr.metrics.tool_calls_total.labels(
                tool="get_movie_status", status="radarr_not_configured"
            ).inc()
            return text_result(
                "Radarr isn't configured yet — ask the admin to set RADARR_URL and RADARR_API_KEY.",
                is_error=True,
            )

        lookup = await find_user_request(overseerr_client, identity, settings, title_input)

        if lookup.status != "ok":
            cleanrr.metrics.tool_calls_total.labels(
                tool="get_movie_status", status=lookup.status
            ).inc()
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
                return text_result(
                    "Tell me which title you're asking about.",
                    is_error=False,
                )
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
                    cleanrr.metrics.tool_calls_total.labels(
                        tool="get_movie_status", status="http_error"
                    ).inc()
                    return text_result("An error occurred — try again later.", is_error=True)
                count = len(lookup.candidates)
                lines = [f"Found {count} possible matches — which one?"]
                for req in lookup.candidates:
                    media = req.get("media", {})
                    title = media.get("title") or media.get("name")
                    year = media.get("releaseYear")
                    if year:
                        lines.append(f"- {title} ({year})")
                    else:
                        lines.append(f"- {title}")
                return text_result("\n".join(lines), is_error=False)

        if lookup.request is None:
            cleanrr.metrics.tool_calls_total.labels(
                tool="get_movie_status", status="http_error"
            ).inc()
            return text_result(
                "An error occurred while fetching movie status — try again in a moment.",
                is_error=True,
            )

        media = lookup.request.get("media", {})
        tmdb_id = media.get("tmdbId")
        if tmdb_id is None:
            cleanrr.metrics.tool_calls_total.labels(
                tool="get_movie_status", status="not_a_movie"
            ).inc()
            return text_result(
                "That looks like a TV show — try asking about its episodes.",
                is_error=False,
            )
        if not isinstance(tmdb_id, int):
            cleanrr.metrics.tool_calls_total.labels(
                tool="get_movie_status", status="parse_error"
            ).inc()
            return text_result(
                "Unexpected response format from Overseerr — try again later.",
                is_error=True,
            )

        try:
            base_url = str(settings.radarr_url).rstrip("/")
            movie_resp = await radarr_client.get(
                f"{base_url}/api/v3/movie",
                params={"tmdbId": tmdb_id},
            )
            if movie_resp.status_code != 200:
                cleanrr.metrics.tool_calls_total.labels(
                    tool="get_movie_status", status="http_error"
                ).inc()
                return text_result(
                    "Couldn't reach Radarr — try again in a moment.",
                    is_error=True,
                )

            try:
                movie_data = movie_resp.json()
            except ValueError:
                cleanrr.metrics.tool_calls_total.labels(
                    tool="get_movie_status", status="parse_error"
                ).inc()
                return text_result(
                    "Unexpected response format from Radarr — try again later.",
                    is_error=True,
                )

            if not movie_data:
                cleanrr.metrics.tool_calls_total.labels(
                    tool="get_movie_status", status="not_in_radarr"
                ).inc()
                return text_result(
                    "Overseerr has the request but Radarr hasn't picked it up yet. "
                    "Try again in a few minutes.",
                    is_error=False,
                )

            movie = movie_data[0]
            if not isinstance(movie, dict):
                cleanrr.metrics.tool_calls_total.labels(
                    tool="get_movie_status", status="parse_error"
                ).inc()
                return text_result(
                    "Unexpected response format from Radarr — try again later.",
                    is_error=True,
                )

            movie_id = movie.get("id")
            title = movie.get("title")
            year = movie.get("year")
            has_file = movie.get("hasFile", False)

            if movie_id is None or title is None:
                cleanrr.metrics.tool_calls_total.labels(
                    tool="get_movie_status", status="parse_error"
                ).inc()
                return text_result(
                    "Unexpected response format from Radarr — try again later.",
                    is_error=True,
                )

            queued = 0
            try:
                queue_resp = await radarr_client.get(
                    f"{base_url}/api/v3/queue",
                    params={"movieId": movie_id, "pageSize": 10},
                )
                if queue_resp.status_code == 200:
                    try:
                        queue_data = queue_resp.json()
                        if isinstance(queue_data, dict):
                            queued = len(queue_data.get("records", []))
                    except ValueError:
                        pass
            except httpx.HTTPError:
                logger.exception("Radarr queue fetch failed")

            title_year = f"{title} ({year})" if year else title

            if has_file:
                result_text = f"{title_year} is downloaded."
            elif queued > 0:
                result_text = f"{title_year}: downloading."
            else:
                result_text = f"{title_year}: nothing yet — Radarr is searching."

            cleanrr.metrics.tool_calls_total.labels(tool="get_movie_status", status="success").inc()
            return text_result(result_text, is_error=False)

        except httpx.HTTPError:
            logger.exception("Radarr HTTP error")
            cleanrr.metrics.tool_calls_total.labels(
                tool="get_movie_status", status="http_error"
            ).inc()
            return text_result(
                "An error occurred while fetching movie status — try again in a moment.",
                is_error=True,
            )

    return [get_movie_status]
