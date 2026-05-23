# Phase 4.3 Spec — Sonarr TV Show Status

## Goal

Friend asks "is The Bear downloading?" → bot fuzzy-matches their Overseerr requests, finds the corresponding Sonarr series, returns episode availability + active downloads.

Output: "The Bear: 22 of 30 episodes ready, 2 downloading."

## Architecture Decisions (Locked)

1. **Sonarr-only-with-Overseerr gate**: Sonarr has no per-user concept — shows aren't owned by anyone. To answer "my show", we have to cross-reference through Overseerr's request log (which IS per-user). So `get_show_status` only registers when BOTH Sonarr and Overseerr are configured. Sonarr-without-Overseerr is meaningless for this audience.

2. **Extract find_user_request helper**: The cross-reference flow (telegram → Overseerr username → user's requests → fuzzy match title) is needed by `find_my_request` and `get_show_status`. Extract before duplicating.

3. **Flat file layout**: Add `cleanrr/tools/sonarr.py` alongside `cleanrr/tools/overseerr.py`. No `tools/<service>/` subdirs — at 4 services this stays readable, and the shared helpers (`_context`, `_results`, `_user_request`) already live at the flat level.

4. **Series identity via tvdbId**: Overseerr stores `media.tvdbId` on every TV request. Sonarr supports `GET /api/v3/series?tvdbId={id}`. No title-based search needed.

## Files to Create

### cleanrr/tools/_user_request.py

Dataclass and helper function for cross-referencing telegram user → Overseerr request.

```python
@dataclass
class UserRequestLookup:
    status: Literal[
        "ok", "not_configured", "context_missing", "unlinked_user",
        "empty_input", "user_not_found", "http_error", "parse_error",
        "no_match", "multi_match"
    ]
    request: dict[str, Any] | None = None       # populated on "ok"
    candidates: list[dict[str, Any]] | None = None  # populated on "multi_match"

async def find_user_request(
    overseerr_client: httpx.AsyncClient | None,
    identity: Identity,
    settings: Settings,
    title: str,
) -> UserRequestLookup:
    """Cross-reference telegram user → Overseerr request matching title.

    Caller must increment its own tool_calls_total metric based on returned status.
    """
```

Implements the existing 4.2 flow: configured check, ContextVar, get_link, empty-input, _resolve_user_id, requests fetch, year-stripped fuzzy match. Returns structured result; does NOT increment metrics or format text — that's the caller's job.

Also define and move `_resolve_user_id()` here from overseerr.py.

Keep module-level constants:
- `_REQUEST_FETCH_LIMIT = 50`
- `_FUZZY_MATCH_CUTOFF = 0.4`
- `_YEAR_PATTERN = re.compile(r"\s*\(?\b(19|20)\d{2}\b\)?\s*$")`

### cleanrr/tools/_status_label.py

Move `_format_status_label()` from overseerr.py here (it's shared between overseerr.py and sonarr.py now).

```python
def _format_status_label(req_status: int | None, media_status: int | None) -> str:
    # Same signature and behavior as in overseerr.py
```

### cleanrr/tools/sonarr.py

New file with Sonarr tool.

```python
def build_tools(
    sonarr_client: httpx.AsyncClient,
    overseerr_client: httpx.AsyncClient,
    identity: Identity,
    settings: Settings,
) -> list[SdkMcpTool]:

    @tool(
        "get_show_status",
        "Look up the download status of a TV show the user requested. "
        "Use when the user asks 'is X downloading?', 'is my show ready?', 'how many episodes of Y are available?'. "
        "Pass the show title as the user said it.",
        {"title": str},
    )
    async def get_show_status(args: dict[str, Any]) -> dict[str, Any]:
        ...
```

Behavior (each exit increments `tool_calls_total{tool="get_show_status", status=...}`):

**a.** Sonarr not configured → `status=not_configured`, "Sonarr isn't configured yet — ask the admin."

**b.** Call `find_user_request()`. Map each lookup status straight through to the metric label, returning a status-appropriate `text_result()`:
   - `not_configured`, `context_missing`, `unlinked_user`, `empty_input`, `user_not_found`, `http_error`, `parse_error`, `no_match`, `multi_match` — render the same `text_result()` as `find_my_request()` does today (multi_match uses the disambiguation list from `lookup.candidates`)

**c.** Status ok: Extract `tvdb_id = lookup.request["media"]["tvdbId"]`. If missing or null → `status=not_a_show`, "That looks like a movie — Radarr support lands in the next phase.", `is_error=False`.

**d.** `GET {sonarr_url}/api/v3/series?tvdbId={tvdb_id}`. Non-200 → `status=http_error`. JSON parse error → `status=parse_error`. Empty result list → `status=not_in_sonarr`, "Overseerr has the request but Sonarr hasn't picked it up yet. Try again in a few minutes.", `is_error=False`.

**e.** Take `series = response[0]`. Extract `series_id = series["id"]`, `title = series["title"]`, `stats = series.get("statistics", {})`. Treat missing/non-dict statistics as zero counts (defensive).

**f.** `GET {sonarr_url}/api/v3/queue?seriesId={series_id}&pageSize=50`. Non-200 → still return the series-level summary without queue info; don't fail the whole tool on a queue-fetch error. Log the queue fetch error and continue.

**g.** Build summary using:
   - `total = stats.get("episodeCount", 0)` — episodes that exist in TVDB up to today
   - `have = stats.get("episodeFileCount", 0)` — on disk
   - `queued = len(queue_records)` if queue succeeded else `None`

Output formatting:
- `have == total and total > 0` → "All N episodes of <title> are downloaded."
- `queued > 0` → "<title>: H of T episodes ready, Q downloading."
- `have == 0 and queued == 0` → "<title>: nothing downloaded yet — Sonarr is searching."
- otherwise → "<title>: H of T episodes ready."

Increment `status=success`, `is_error=False`.

## Files to Modify

### cleanrr/tools/overseerr.py

- Refactor `find_my_request()` to call `find_user_request()` helper, then switch on the result's status to increment the metric and render the text.
- Delete now-unused inline fuzzy-matching code.
- Delete `_resolve_user_id()` (moved to `_user_request.py`).
- Delete `_format_status_label()` (moved to `_status_label.py`).
- Update imports: add `from cleanrr.tools._user_request import find_user_request, _resolve_user_id` and `from cleanrr.tools._status_label import _format_status_label`.
- `list_my_requests()` is unaffected.

### cleanrr/agent.py

- Import: `from cleanrr.tools.sonarr import build_tools as build_sonarr_tools`
- In `Agent.start()`:
  - Create `sonarr_client` (httpx.AsyncClient) similar to how overseerr_client is created
  - Add to AsyncExitStack
  - Build sonarr_tools: `build_sonarr_tools(sonarr_client, overseerr_client, identity, settings)` ONLY if `sonarr_url AND sonarr_api_key AND overseerr_url AND overseerr_api_key` all set
  - Append sonarr_tools to tools list
  - Append sonarr tool names to allowed_tools
- Update system prompt's "Tools available" section: add get_show_status with a one-liner

### cleanrr/config.py

Add three settings:
- `sonarr_url: HttpUrl | None = None`
- `sonarr_api_key: SecretStr | None = None`
- `sonarr_timeout_seconds: float = 10.0`

### .env.example

Document the three new vars under a "Sonarr (optional)" block, mirroring the Overseerr block. Example:
```
# Sonarr (optional)
# SONARR_URL=http://sonarr:8989
# SONARR_API_KEY=...
# SONARR_TIMEOUT_SECONDS=10
```

### README.md

- Under "What it does today" add: "TV show status via Sonarr — see what's downloaded and what's downloading."
- Under "What it doesn't do yet" Phase 4 line: change "Sonarr / Radarr / qBittorrent tools" to "Radarr / qBittorrent tools" (Sonarr now done)

## Tests to Create

### tests/test_user_request.py

Test the extracted helper directly (no @tool wrapper). One test per status branch (10 tests):
- `test_find_user_request_ok` — returns ok with request dict
- `test_find_user_request_not_configured` — Overseerr not configured
- `test_find_user_request_context_missing` — ContextVar not set
- `test_find_user_request_unlinked_user` — no link found
- `test_find_user_request_empty_input` — empty title
- `test_find_user_request_user_not_found` — user lookup fails
- `test_find_user_request_http_error` — HTTP error on requests
- `test_find_user_request_parse_error` — JSON parse error
- `test_find_user_request_no_match` — fuzzy match no matches
- `test_find_user_request_multi_match` — fuzzy match multiple matches

Reuse the mock_identity / mock_client / settings fixture pattern from test_tools_overseerr.py.

### tests/test_tools_sonarr.py

12 tests for get_show_status:
- `test_get_show_status_sonarr_not_configured` — Sonarr URL/key missing
- `test_get_show_status_user_request_errors` — parametrized: each find_user_request status (unlinked_user, empty_input, no_match, multi_match, etc.) passes through with matching metric label and text
- `test_get_show_status_not_a_show` — Overseerr request lacks tvdbId
- `test_get_show_status_not_in_sonarr` — Sonarr returns empty series array
- `test_get_show_status_series_http_error` — 500 on series fetch
- `test_get_show_status_series_parse_error` — malformed JSON on series
- `test_get_show_status_all_downloaded` — have==total → "All N episodes…"
- `test_get_show_status_partial_with_queue` — H/T + Q downloading
- `test_get_show_status_nothing_yet` — have==0, queue==0 → "nothing downloaded yet"
- `test_get_show_status_partial_no_queue` — H/T with empty queue
- `test_get_show_status_queue_fetch_fails_still_returns_series` — queue 500 doesn't fail the tool (series summary still returned without queue info)
- `test_get_show_status_increments_metric_on_every_exit` — parametrized symmetric-paths guard

## Modify Existing Tests

### tests/test_agent.py

Add 2 tests:
- `test_start_registers_sonarr_tools_when_both_configured` — Sonarr+Overseerr set → `get_show_status` in allowed_tools
- `test_start_skips_sonarr_tools_when_overseerr_missing` — Sonarr set, Overseerr unset → no `get_show_status`

## Acceptance Criteria

1. `pytest`, `pyright`, `ruff`, `bandit` all clean
2. Patch coverage ≥85% (codecov gate from #47)
3. `jscpd` ≤5% (jscpd gate from #47)
4. All existing 4.1 / 4.2 tests pass unchanged
5. `cleanrr_tool_calls_total{tool="get_show_status", status=...}` visible across all 12 exit labels
6. Sonarr tool gated on BOTH services configured

## Out of Scope

- Per-episode queries ("is S3E5 ready?") — defer until users actually ask
- Radarr (Phase 4.4)
- qBittorrent torrent-level detail (Phase 4.5)
- Sonarr write actions (Phase 5)
- Caching tvdbId → seriesId lookups (premature)
- Refactor to per-service subdirs (premature at 4 services)
