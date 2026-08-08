# Architecture

## Overview

```
Telegram user ──DM──> Telegram API ──> cleanrr (Docker)
                                          │
                                          ├─ Claude Agent SDK ── reasoning
                                          │
                                          └─ tool layer (Phase 4+) ──> Sonarr / Radarr
                                                                       Overseerr
                                                                       qBittorrent
```

Single Python process, single container. Tools are defined as in-process `@tool` functions on the Agent SDK — no separate MCP server processes to run. Each Telegram user gets their own dedicated `Agent` (one Claude CLI subprocess), lazily created and capped by `AgentPool` — see [Project layout](#project-layout) below.

## Actors

- **Unlinked Telegram user** — can run `/start`, `/help`, and `/link <code>`. No access to any tool.
- **Linked user ("owner")** — anyone who has redeemed a link code. Can chat naturally with the bot, look up their own Overseerr requests and Sonarr/Radarr status, and — behind a confirm/cancel prompt — cancel their own requests or re-trigger a search on their own stuck requests. Ownership is checked at the tool layer against what Overseerr lists as theirs; a linked user cannot act on someone else's request.
- **Admin** — a linked user whose Telegram ID is in `ADMIN_TELEGRAM_IDS`. Can additionally run `/invite` to issue link codes, run stalled-torrent diagnostics, and — behind a confirmation prompt — delete a torrent and its files from qBittorrent.
- **cleanrr (the bot process)** — orchestrates the above: receives the Telegram message, forwards conversation state to Claude, executes whichever `@tool` calls Claude requests, and enforces the confirmation gate before any destructive tool actually runs.
- **Claude / Anthropic API** — the reasoning engine. Receives conversation content and tool *definitions*, returns text and tool-call requests. It does not have direct network access to the homelab — cleanrr's tool layer is what actually executes the Sonarr/Radarr/Overseerr/qBittorrent calls.
- **Overseerr, Sonarr, Radarr, qBittorrent** — backend systems cleanrr calls via their own REST/WebUI APIs, authenticated with admin-scoped API keys stored in `.env`.

## External interfaces

| Interface | Direction | Auth | Notes |
| --- | --- | --- | --- |
| Telegram Bot API | Inbound | Bot token | The only interface exposed to end users. |
| Anthropic API (or Claude subscription auth) | Outbound | OAuth token or API key | Reasoning only — never sees homelab credentials. |
| Overseerr REST API | Outbound | API key | Request lookup, cancellation. |
| Sonarr / Radarr REST API | Outbound | API key | Status lookup, re-search trigger. |
| qBittorrent WebUI API | Outbound | Username/password | Stalled-torrent diagnostics, deletion. |
| Prometheus `/metrics` | Inbound, optional | None (bind to `127.0.0.1` by default) | Opt-in via `METRICS_ENABLED`; see [README's Metrics section](README.md#metrics-optional). |

## Project layout

```
cleanrr/
├── __main__.py        # entrypoint (python -m cleanrr)
├── bot.py             # application wiring + lifecycle (startup/shutdown)
├── handlers.py        # Telegram command/message/callback handlers
├── agent.py           # one Agent = one dedicated Claude subprocess per user
├── agent_pool.py      # AgentPool: creates/caps one Agent per telegram_user_id
├── identity.py        # SQLite link-code store + Telegram↔Overseerr mapping
├── metrics.py         # Prometheus metrics (opt-in)
├── config.py          # pydantic-settings + auth validation
├── permissions/       # destructive-action confirmation flow (registry,
│                      #   prompt formatters, can_use_tool callback)
└── tools/             # read + write @tool wrappers for Overseerr/Sonarr/Radarr/qBittorrent
```
