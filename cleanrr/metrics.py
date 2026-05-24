from prometheus_client import Counter, Gauge, Histogram, start_http_server

up = Gauge("cleanrr_up", "1 when the bot is running")

telegram_messages_total = Counter(
    "cleanrr_telegram_messages_total",
    "Telegram messages received",
    ["kind", "command"],
)

claude_requests_total = Counter(
    "cleanrr_claude_requests_total",
    "Requests forwarded to Claude",
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

tool_calls_total = Counter(
    "cleanrr_tool_calls_total",
    "Calls to in-process MCP tools",
    ["tool", "status"],
)

destructive_actions_total = Counter(
    "cleanrr_destructive_actions_total",
    "Destructive tool invocations by tool and confirmation outcome",
    ["tool", "outcome"],
)


def start(port: int, addr: str = "127.0.0.1") -> None:
    up.set(1)
    start_http_server(port, addr=addr)
