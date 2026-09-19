from prometheus_client import Counter, Gauge, Histogram, start_http_server

up = Gauge("cleanrr_up", "1 when the bot is running")

telegram_messages_total = Counter(
    "cleanrr_telegram_messages_total",
    "Telegram messages received",
    ["kind", "command"],
)

claude_requests_total = Counter(
    "cleanrr_claude_requests_total",
    # Allowed status values: unauthorized | rejected_too_long | at_capacity |
    # timeout | error | delivery_failed | success.
    # get_linked_user raising skips this counter; the gap surfaces in
    # telegram_messages_total{kind="text"}.
    "Inbound Telegram messages by outcome",
    ["status"],
)

claude_request_duration_seconds = Histogram(
    "cleanrr_claude_request_duration_seconds",
    "End-to-end Claude request latency",
)

link_codes_issued_total = Counter(
    "cleanrr_link_codes_issued_total",
    "Link codes issued by /invite",
)

link_codes_redeemed_total = Counter(
    "cleanrr_link_codes_redeemed_total",
    "Link code redemption attempts",
    ["status"],
)

linked_users = Gauge(
    "cleanrr_linked_users",
    "Number of confirmed Telegram → Overseerr mappings",
)

links_missing_overseerr_user_id = Gauge(
    "cleanrr_links_missing_overseerr_user_id",
    "Linked users still resolved by Overseerr username instead of a stored user id",
)

tool_calls_total = Counter(
    "cleanrr_tool_calls_total",
    # status is a fixed per-tool vocabulary; unauthorized and reset are
    # stamped by can_use_tool before any prompt is sent.
    "Calls to in-process MCP tools",
    ["tool", "status"],
)

destructive_actions_total = Counter(
    "cleanrr_destructive_actions_total",
    # Allowed outcome values: confirmed | denied | timed_out (see permissions.Outcome).
    # Pre-confirmation rejections (admin gates, ownership) belong on tool_calls_total.
    "Destructive tool invocations by tool and confirmation outcome",
    ["tool", "outcome"],
)

agent_pool_agents = Gauge(
    "cleanrr_agent_pool_agents",
    "Agents in the pool right now, one Claude CLI subprocess each",
)

agent_evictions_total = Counter(
    "cleanrr_agent_evictions_total",
    # Allowed reason values: idle | reset (see agent_pool.EvictionReason).
    # Shutdown stops every Agent but is not an eviction and is not counted here.
    "Agents removed from the pool before shutdown, by reason",
    ["reason"],
)


def start(port: int, addr: str = "127.0.0.1") -> None:
    up.set(1)
    start_http_server(port, addr=addr)
