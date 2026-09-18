# Code standards

How code is written in cleanrr. [`DESIGN_PRINCIPLES.md`](DESIGN_PRINCIPLES.md) covers what gets built; this covers the shape of the code. The short checklist agents load automatically lives in `.claude/rules/`; this document is the rationale and the examples.

## Toolchain

| Tool | Role | Invocation |
| --- | --- | --- |
| ruff | lint + format, line length 100, `py312` target | `python -m ruff check .` / `python -m ruff format .` |
| pyright | type check, `strict` | `python -m pyright` |
| bandit | security smells | `python -m bandit -r cleanrr/ -ll` |
| pytest | tests, `asyncio_mode = "auto"` | `python -m pytest` |

`bash .claude/hooks/gate.sh` runs all of them with the project's interpreter and is what CI, the coder agent, and you should run before calling anything done. Python 3.12 is the floor; CI runs 3.12 through 3.14.

## Formatting and layout

ruff formats; nobody argues about style. Modules are single-responsibility and stay under about 150 lines; when one grows past that, split it (the `permissions/` package is the model). Private helpers shared across a package start with `_` and live in a `_name.py` sibling (`tools/_user_request.py`, `tools/_results.py`); importing them across the package is deliberate, not a leak.

## Types

Type hints on every signature. `Any` appears only where JSON enters the process and is narrowed at the first `isinstance` check. `Literal` types define small vocabularies that are also metric label values (`permissions.Outcome`, `UserRequestLookup.status`), so a typo is a type error rather than a new Prometheus series. The pyright `reportUnknown*` suppressions in `pyproject.toml` exist for one reason, documented there: the Agent SDK's `@tool` generic. Don't widen them.

## Comments and docstrings

Default to none. A comment exists only when the *why* is non-obvious: a library quirk, a hidden constraint, a subtle invariant. Match the existing terse one-liners. Rationale that would take a paragraph belongs in the commit or PR body.

```python
# Sonarr's queue endpoint filters on seriesIds (plural, array-bound);
# seriesId is silently ignored and returns the whole instance's queue.
params = {"seriesIds": [series_id], "pageSize": 50}
```

Docstrings go on public entry points and on classes whose role isn't obvious from the name (`Agent`, `ConfirmationRegistry`). They say what the thing is for, not restate the signature. No Google-style `Args:`/`Returns:` blocks; the types already say that.

## Async and I/O

All I/O in the event loop is async: `httpx.AsyncClient`, `aiosqlite`, `asyncio.wait_for`. Never `requests`, `sqlite3`, or `time.sleep` in a handler. Every `httpx` client is constructed with an explicit `timeout`; every long await that could outlive the caller (a Telegram send inside a tool, a formatter lookup) is individually bounded, because the SDK doesn't cancel tool tasks when the outer request times out.

Shared clients are built once in `bot.py` and injected; a module never builds its own `AsyncClient`.

## Errors at the boundary

Tools never raise into the SDK. They return `text_result(text, is_error=...)` and increment `metrics.tool_calls_total` exactly once per exit path:

```python
if resp.status_code != 200:
    metrics.tool_calls_total.labels(tool="get_movie_status", status="http_error").inc()
    return text_result("Couldn't reach Radarr — try again in a moment.", is_error=True)
```

`is_error=True` is for failures the user can't act on (upstream down, malformed response). A "not linked yet" or "no match" is `is_error=False` with an instruction the user can follow. Catch the specific exception (`httpx.HTTPError`, `ValueError`, `TelegramError`); a broad `except Exception` is only for the outermost handler that turns an unknown crash into a graceful reply plus `logger.exception`.

## Untrusted data

Telegram text, Claude's tool arguments, and every upstream response are untrusted:

- Shape-check JSON before use (`isinstance(data, dict)`, `isinstance(tmdb_id, int)`); default-deny on surprises.
- Bound upstream strings before they reach a Telegram reply (`[:80]`) and strip newlines before they reach a log line.
- Validate tool arguments in the tool (`normalize_torrent_hash`, `request_id > 0`); the schema is a hint to the model, not a guarantee.
- Never pass user text into a shell, a file path, a SQL string, or an HTTP body unbounded.
- Every token-shaped setting is `SecretStr`; `.get_secret_value()` never appears in an f-string.

## Metrics

`Counter` for events that only go up; `Gauge` for current state. An upsert (`ON CONFLICT DO UPDATE`) is state, not an event: `Gauge.set(count)`, not `Counter.inc()`. Label values come from a `Literal` or a constant, never from user or upstream data. Instrument every path symmetrically: if `/link` counts, `/invite` counts; if success observes latency, so do timeout and error.

## Configuration

Anything an operator might change is a `Settings` field in `config.py` with a `description`, a row in `.env.example`, and a row in README, kept in sync by `tests/test_consistency.py`. Fields that parse a custom string (comma-separated ids) use `Annotated[..., NoDecode]`, because pydantic-settings JSON-decodes complex types before validators run. Internals (pool sizes, sweeper intervals) are module constants with a one-line reason.

## SQL

`aiosqlite` only. Parameterised `?` placeholders; no f-strings in SQL. Read-then-write is a race; use `ON CONFLICT ... DO UPDATE` for upserts and `UPDATE ... WHERE ... RETURNING` for check-and-consume. Timestamps are integer epoch seconds. Tables are created `IF NOT EXISTS` in `start()`; there is no migration layer until there are five tables.

## Tests

One test file per source module. HTTP is mocked with `AsyncMock(spec=httpx.AsyncClient)` returning `httpx.Response` objects, not with `MockTransport`. Tests assert behaviour, including what the code does with input it wasn't designed for: malformed JSON, an oversize title, a wrong-type argument, a 403 mid-session. When a tool's output feeds another tool, assert the identifier is present (`"(request_id: 42)"`), not only the title. Every bug found live gets a regression test named for the behaviour, not the ticket.

The SDK client is mocked everywhere, so stream and subprocess semantics are invisible to the suite; a change to `agent.py` says so in its PR and is verified live.

## Git

Conventional Commits, subject at most 50 characters, imperative, no trailing period; body only when the why isn't obvious from the diff. Every commit is signed off (`git commit -s`) — the DCO check is a required status. No attribution trailers of any kind. Commit and PR text describes the change in a maintainer's plain voice: symptom, cause, fix. It never narrates how the change was arrived at or who asked for it; `.claude/rules/commit-style.md` lists the phrasings to avoid. `CHANGELOG.md` is generated by release-please and never edited by hand; a user-visible change lands as `feat:` or `fix:` so it appears there.

## Review checklist

- Does every new exit path increment a metric with a label from the fixed vocabulary?
- Does every new `Settings` field have `.env.example` and README rows?
- Does a new tool appear in `DEFAULT_SYSTEM_PROMPT`, and if destructive, in `WRITE_TOOLS`?
- Does the tool's output carry the ids the next tool needs, and does a test assert them?
- Is every upstream string bounded before it reaches Telegram or a log?
- Did `ARCHITECTURE.md` or `THREAT_MODEL.md` need a line, and did it get one?
