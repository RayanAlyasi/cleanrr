---
paths:
  - "cleanrr/tools/**/*.py"
  - "cleanrr/agent.py"
  - "cleanrr/permissions/**/*.py"
---

# Tool invariants — cleanrr

These are the load-bearing facts a new or changed tool must keep true. Each one has shipped a bug or is the reason a control holds.

- Every `@tool` is built inside a `build_tools(...)` factory that closes over `telegram_user_id`. No module-level "current user" state.
- A destructive tool lives in `cleanrr/tools/<service>_write.py` **and** is listed in `WRITE_TOOLS` in `cleanrr/permissions/_callback.py`. A tool missing from that set is auto-allowed by the SDK and runs with no confirmation.
- Admin-only destructive tools are also in `ADMIN_ONLY_TOOLS`, and re-check `telegram_user_id in settings.admin_telegram_ids` at mutation time.
- `ClaudeAgentOptions.tools` stays `[]` and `allowed_tools` never contains a `WRITE_TOOLS` member (see `Agent.start`).
- Every tool is described in `DEFAULT_SYSTEM_PROMPT` under "Tools available", with when to use it and what to pass.
- A tool's text result includes every identifier a follow-up tool needs (`request_id`, torrent hash). Tests assert the identifier, not just the title.
- Results go through `text_result(..., is_error=...)`; a tool never raises into the SDK. `is_error=True` only for failures the user can't act on themselves.
- Each exit path increments `metrics.tool_calls_total` exactly once, with a `status` label drawn from a small fixed vocabulary; confirmation outcomes go on `destructive_actions_total` only.
- Upstream JSON is shape-checked before use; upstream strings are bounded (`[:80]`) before interpolation and newline-stripped before logging.
- Ownership of a request is verified against Overseerr's `requestedBy.id`, not against the argument Claude passed.
- Overseerr list endpoints never embed titles; resolve via `_fetch_media_details`/`enrich_titles_with_names` rather than reading `media["title"]` from a list response.
