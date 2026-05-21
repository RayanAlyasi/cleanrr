import logging
from typing import Any

import httpx
from claude_agent_sdk import SdkMcpTool, tool

import cleanrr.metrics
from cleanrr.config import Settings
from cleanrr.identity import Identity
from cleanrr.tools._context import current_telegram_user_id
from cleanrr.tools._results import text_result

logger = logging.getLogger(__name__)


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
            # 4. Resolve username → user ID
            user_search = await client.get(
                f"{settings.overseerr_url}/api/v1/user",
                params={"q": overseerr_username, "take": 1},
            )
            if user_search.status_code == 404:
                cleanrr.metrics.tool_calls_total.labels(
                    tool="list_my_requests", status="user_not_found"
                ).inc()
                return text_result(
                    "Couldn't find your Overseerr account — admin may need to re-issue the link.",
                    is_error=False,
                )
            if user_search.status_code != 200:
                cleanrr.metrics.tool_calls_total.labels(
                    tool="list_my_requests", status="http_error"
                ).inc()
                return text_result(
                    "Couldn't reach Overseerr — try again in a moment.",
                    is_error=True,
                )

            try:
                user_data = user_search.json()
                users = user_data.get("results", [])
            except ValueError:
                cleanrr.metrics.tool_calls_total.labels(
                    tool="list_my_requests", status="parse_error"
                ).inc()
                return text_result(
                    "Unexpected response format from Overseerr — try again later.",
                    is_error=True,
                )

            if not users:
                cleanrr.metrics.tool_calls_total.labels(
                    tool="list_my_requests", status="user_not_found"
                ).inc()
                return text_result(
                    "Couldn't find your Overseerr account — admin may need to re-issue the link.",
                    is_error=False,
                )

            user_id = users[0]["id"]

            # 5. Fetch requests
            requests_resp = await client.get(
                f"{settings.overseerr_url}/api/v1/user/{user_id}/requests",
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

                # Format status label
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

                status_label = ", ".join(status_parts) if status_parts else "unknown"

                # Format title
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

    return [list_my_requests]
