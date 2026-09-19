from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx
from claude_agent_sdk import SdkMcpTool, tool

import cleanrr.metrics as metrics
from cleanrr.config import Settings
from cleanrr.identity import Identity
from cleanrr.tools._queue_reason import diagnose_queue, render_queue_reason
from cleanrr.tools._results import text_result
from cleanrr.tools._untrusted import bound_text
from cleanrr.tools._user_request import find_user_request, render_lookup_error

if TYPE_CHECKING:
    import telegram

logger = logging.getLogger(__name__)


def _episode_count(stats: object, key: str) -> int:
    # A non-dict `statistics` used to raise AttributeError out of the handler,
    # and a non-int count used to be interpolated into the reply unbounded.
    if not isinstance(stats, dict):
        return 0
    value = stats.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def build_tools(
    sonarr_client: httpx.AsyncClient,
    overseerr_client: httpx.AsyncClient,
    identity: Identity,
    settings: Settings,
    *,
    telegram_user_id: int,
    telegram_bot: telegram.Bot | None = None,
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

        if settings.sonarr_url is None or settings.sonarr_api_key is None:
            metrics.tool_calls_total.labels(
                tool="get_show_status", status="sonarr_not_configured"
            ).inc()
            return text_result(
                "Sonarr isn't configured yet — ask the admin to set SONARR_URL and SONARR_API_KEY.",
                is_error=True,
            )

        lookup = await find_user_request(
            overseerr_client,
            identity,
            settings,
            title_input,
            telegram_user_id=telegram_user_id,
            telegram_bot=telegram_bot,
        )

        error_response = render_lookup_error(lookup, title_input)
        if error_response is not None:
            metrics.tool_calls_total.labels(tool="get_show_status", status=lookup.status).inc()
            return error_response

        if lookup.request is None:
            metrics.tool_calls_total.labels(tool="get_show_status", status="http_error").inc()
            return text_result(
                "An error occurred while fetching show status — try again in a moment.",
                is_error=True,
            )

        media = lookup.request.get("media", {})
        tvdb_id = media.get("tvdbId")
        if not tvdb_id:
            metrics.tool_calls_total.labels(tool="get_show_status", status="not_a_show").inc()
            return text_result(
                "That looks like a movie — try asking about its download status.",
                is_error=False,
            )

        try:
            base_url = str(settings.sonarr_url).rstrip("/")
            series_resp = await sonarr_client.get(
                f"{base_url}/api/v3/series",
                params={"tvdbId": tvdb_id},
            )
            if series_resp.status_code != 200:
                metrics.tool_calls_total.labels(tool="get_show_status", status="http_error").inc()
                return text_result(
                    "Couldn't reach Sonarr — try again in a moment.",
                    is_error=True,
                )

            try:
                series_data = series_resp.json()
            except ValueError:
                metrics.tool_calls_total.labels(tool="get_show_status", status="parse_error").inc()
                return text_result(
                    "Unexpected response format from Sonarr — try again later.",
                    is_error=True,
                )

            if not series_data:
                metrics.tool_calls_total.labels(
                    tool="get_show_status", status="not_in_sonarr"
                ).inc()
                return text_result(
                    "Overseerr has the request but Sonarr hasn't picked it up yet. "
                    "Try again in a few minutes.",
                    is_error=False,
                )

            series = series_data[0]
            if not isinstance(series, dict):
                metrics.tool_calls_total.labels(tool="get_show_status", status="parse_error").inc()
                return text_result(
                    "Unexpected response format from Sonarr — try again later.",
                    is_error=True,
                )
            series_id = series.get("id")
            title = series.get("title")
            if series_id is None or title is None:
                metrics.tool_calls_total.labels(tool="get_show_status", status="parse_error").inc()
                return text_result(
                    "Unexpected response format from Sonarr — try again later.",
                    is_error=True,
                )
            # API-supplied title is untrusted; bound length before interpolation.
            title = bound_text(title, limit=80, default="Unknown")

            total = _episode_count(series.get("statistics"), "episodeCount")
            have = _episode_count(series.get("statistics"), "episodeFileCount")

            # Don't fail the tool if this errors — episode counts alone are still useful.
            queue_records: object = []
            queue_read_ok = False
            try:
                queue_resp = await sonarr_client.get(
                    f"{base_url}/api/v3/queue",
                    # Sonarr's queue endpoint filters on seriesIds (plural, array-
                    # bound) — seriesId is silently ignored and returns the whole
                    # instance's queue instead of just this show's.
                    params={"seriesIds": [series_id], "pageSize": 50},
                )
                if queue_resp.status_code == 200:
                    try:
                        queue_data = queue_resp.json()
                        if isinstance(queue_data, dict):
                            queue_records = queue_data.get("records", [])
                            queue_read_ok = True
                    except ValueError:
                        pass
            except httpx.HTTPError:
                logger.exception("Sonarr queue fetch failed")

            diagnosis = diagnose_queue(queue_records)
            queued = diagnosis.records

            # Format output
            all_downloaded = have == total and total > 0
            if all_downloaded:
                result_text = f"All {total} episodes of {title} are downloaded."
            elif queued > 0:
                queue_word = "downloading" if diagnosis.code == "ok" else "in the queue"
                result_text = f"{title}: {have} of {total} episodes ready, {queued} {queue_word}."
            elif not queue_read_ok:
                result_text = (
                    f"{title}: {have} of {total} episodes ready; Sonarr's queue didn't "
                    "answer, so I can't tell what's downloading."
                )
            elif have == 0 and queued == 0:
                result_text = f"{title}: nothing downloaded yet — Sonarr is searching."
            else:
                result_text = f"{title}: {have} of {total} episodes ready."

            if not all_downloaded:
                reason = render_queue_reason(
                    diagnosis,
                    service="Sonarr",
                    include_download_id=telegram_user_id in settings.admin_telegram_ids,
                )
                if reason:
                    result_text += f"\n{reason}"

            metrics.tool_calls_total.labels(tool="get_show_status", status="success").inc()
            return text_result(result_text, is_error=False)

        except httpx.HTTPError:
            logger.exception("Sonarr HTTP error")
            metrics.tool_calls_total.labels(tool="get_show_status", status="http_error").inc()
            return text_result(
                "An error occurred while fetching show status — try again in a moment.",
                is_error=True,
            )

    return [get_show_status]
