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
    sonarr_client: httpx.AsyncClient,
    overseerr_client: httpx.AsyncClient,
    identity: Identity,
    settings: Settings,
) -> list[SdkMcpTool]:
    """Factory for Sonarr tools."""

    @tool(
        "get_show_status",
        "Look up the download status of a TV show the user requested. "
        "Use when the user asks 'is X downloading?', 'is my show ready?', "
        "'how many episodes of Y are available?'. Pass the show title as the user said it.",
        {"title": str},
    )
    async def get_show_status(args: dict[str, Any]) -> dict[str, Any]:
        title_input = args.get("title", "").strip()

        # Check if Sonarr configured
        if settings.sonarr_url is None or settings.sonarr_api_key is None:
            cleanrr.metrics.tool_calls_total.labels(
                tool="get_show_status", status="sonarr_not_configured"
            ).inc()
            return text_result(
                "Sonarr isn't configured yet — ask the admin to set SONARR_URL and SONARR_API_KEY.",
                is_error=True,
            )

        # Call find_user_request helper
        lookup = await find_user_request(overseerr_client, identity, settings, title_input)

        # Map lookup status to metric label; don't increment here if status=="ok"
        metric_status = lookup.status
        if metric_status == "ok":
            pass  # Will increment after we determine final disposition
        else:
            cleanrr.metrics.tool_calls_total.labels(
                tool="get_show_status", status=metric_status
            ).inc()
            # Pass through non-ok statuses
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
                    "Couldn't find your Overseerr account — admin may need to "
                    "re-issue the link.",
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
                        tool="get_show_status", status="http_error"
                    ).inc()
                    return text_result("An error occurred — try again later.", is_error=True)
                count = len(lookup.candidates)
                disambiguation_lines = [f"Found {count} possible matches — which one?"]
                for req in lookup.candidates:
                    media = req.get("media", {})
                    title = media.get("title") or media.get("name")
                    year = media.get("releaseYear")
                    if year:
                        disambiguation_lines.append(f"- {title} ({year})")
                    else:
                        disambiguation_lines.append(f"- {title}")
                return text_result("\n".join(disambiguation_lines), is_error=False)

        if lookup.request is None:
            cleanrr.metrics.tool_calls_total.labels(
                tool="get_show_status", status="http_error"
            ).inc()
            return text_result(
                "An error occurred while fetching show status — try again in a moment.",
                is_error=True,
            )

        media = lookup.request.get("media", {})
        tvdb_id = media.get("tvdbId")
        if not tvdb_id:
            cleanrr.metrics.tool_calls_total.labels(
                tool="get_show_status", status="not_a_show"
            ).inc()
            return text_result(
                "That looks like a movie — Radarr support lands in the next phase.",
                is_error=False,
            )

        try:
            base_url = str(settings.sonarr_url).rstrip("/")
            series_resp = await sonarr_client.get(
                f"{base_url}/api/v3/series",
                params={"tvdbId": tvdb_id},
            )
            if series_resp.status_code != 200:
                cleanrr.metrics.tool_calls_total.labels(
                    tool="get_show_status", status="http_error"
                ).inc()
                return text_result(
                    "Couldn't reach Sonarr — try again in a moment.",
                    is_error=True,
                )

            try:
                series_data = series_resp.json()
            except ValueError:
                cleanrr.metrics.tool_calls_total.labels(
                    tool="get_show_status", status="parse_error"
                ).inc()
                return text_result(
                    "Unexpected response format from Sonarr — try again later.",
                    is_error=True,
                )

            if not series_data:
                cleanrr.metrics.tool_calls_total.labels(
                    tool="get_show_status", status="not_in_sonarr"
                ).inc()
                return text_result(
                    "Overseerr has the request but Sonarr hasn't picked it up yet. "
                    "Try again in a few minutes.",
                    is_error=False,
                )

            series = series_data[0]
            if not isinstance(series, dict):
                cleanrr.metrics.tool_calls_total.labels(
                    tool="get_show_status", status="parse_error"
                ).inc()
                return text_result(
                    "Unexpected response format from Sonarr — try again later.",
                    is_error=True,
                )
            series_id = series.get("id")
            title = series.get("title")
            if series_id is None or title is None:
                cleanrr.metrics.tool_calls_total.labels(
                    tool="get_show_status", status="parse_error"
                ).inc()
                return text_result(
                    "Unexpected response format from Sonarr — try again later.",
                    is_error=True,
                )
            stats = series.get("statistics", {})

            total = stats.get("episodeCount", 0)
            have = stats.get("episodeFileCount", 0)

            # Fetch queue — don't fail the tool if this errors
            queue_records = []
            try:
                queue_resp = await sonarr_client.get(
                    f"{base_url}/api/v3/queue",
                    params={"seriesId": series_id, "pageSize": 50},
                )
                if queue_resp.status_code == 200:
                    try:
                        queue_data = queue_resp.json()
                        if isinstance(queue_data, dict):
                            queue_records = queue_data.get("records", [])
                    except ValueError:
                        pass
            except httpx.HTTPError:
                logger.exception("Sonarr queue fetch failed")

            queued = len(queue_records)

            # Format output
            if have == total and total > 0:
                result_text = f"All {total} episodes of {title} are downloaded."
            elif queued > 0:
                result_text = f"{title}: {have} of {total} episodes ready, {queued} downloading."
            elif have == 0 and queued == 0:
                result_text = f"{title}: nothing downloaded yet — Sonarr is searching."
            else:
                result_text = f"{title}: {have} of {total} episodes ready."

            cleanrr.metrics.tool_calls_total.labels(tool="get_show_status", status="success").inc()
            return text_result(result_text, is_error=False)

        except httpx.HTTPError:
            logger.exception("Sonarr HTTP error")
            cleanrr.metrics.tool_calls_total.labels(
                tool="get_show_status", status="http_error"
            ).inc()
            return text_result(
                "An error occurred while fetching show status — try again in a moment.",
                is_error=True,
            )

    return [get_show_status]
