# Doc verification — cleanrr

Never assume how a third-party library, SDK, or API behaves from training-time memory. Verify against actually-fetched current docs or source before implementing, specifying, or asserting a claim about it.

**Why:** a single audit night on this codebase found 10+ real, shipped bugs — every one of them traced back to an unverified assumption about a library or API's behavior that turned out to be wrong. None were logic errors caught by tests; all were "the docs say X, but the library actually does Y." See the gotcha list below — each entry is a bug that shipped before it was verified.

## The rule

Before writing code that depends on specific behavior from a library, SDK, or external API — or before asserting in a spec, review, or plan that it behaves a certain way — confirm it against one of:
- The library's current official docs (WebFetch/WebSearch).
- The installed library's actual source (`site-packages/...`, readable locally via `Read`/`Grep` even without network access).
- A live call against the actual external service, when the docs are ambiguous or the service has known deviations from spec (self-hosted forks especially — Jellyseerr vs. vanilla Overseerr, qBittorrent version-to-version response shape changes).

"I'm fairly sure this is how it works" is not verification. If you can't check, say so explicitly rather than asserting confidence.

## How to apply, by role

- **Planning/writing a spec** (orchestrator, Opus): this is the only point in the `/cleanrr-ship` pipeline with full tool access (WebFetch/WebSearch). Any spec line whose correctness depends on library/API behavior must be verified here, before handoff — see `spec-quality.md`. `cleanrr-builder` implements verbatim and cannot research; an unverified assumption baked into the spec ships as-is.
- **Implementing** (`cleanrr-builder`, no WebFetch/WebSearch): you can still read the installed library's actual source via `Read`/`Grep`/`Glob`/`Bash` — do this when a spec's behavior assumption is load-bearing and you can cheaply confirm it. If the source contradicts what the spec assumes, STOP and flag it rather than implementing the assumption anyway.
- **Reviewing/auditing** (`cleanrr-reviewer`, `cleanrr-security`, no WebFetch/WebSearch): keep filing library/SDK/API-behavior-dependent claims under `## Verify` as already required by each agent's contract. The orchestrator must then resolve every `## Verify` item via a research-capable agent before treating the audit as complete — an unresolved `## Verify` item is not the same as "no issue found."

## Gotcha list (verified false assumptions that shipped as bugs)

- **claude-agent-sdk**: a tool name listed in `allowed_tools` auto-approves it and skips `can_use_tool` entirely — never pre-approve anything that needs a confirmation gate.
- **asyncio**: a `contextvars.ContextVar.set()` inside a request handler is invisible to a task created *before* that call (e.g. a persistent background read-loop spawned at connect time). Plain mutable state guarded by a lock is not a free substitute unless the set/reset both happen strictly inside the locked section.
- **python-telegram-bot**: `concurrent_updates` defaults to `False` (sequential processing) — must be explicitly enabled when one handler needs to await a response that only arrives via a different, concurrent update. Once enabled, anything reading shared mutable state across handlers needs to be safe under real concurrency, not just "it worked in testing."
- **Overseerr/Jellyseerr API**: request-list endpoints (`/user/{id}/requests`, `/request/{id}`) never embed title, name, or poster — only tmdbId/tvdbId. Resolve via `/movie/{id}` or `/tv/{id}`. There is no `releaseYear` field anywhere in the schema — only `releaseDate` (movies) / `firstAirDate` (TV shows), both full ISO date strings. The `/user?q=` filter is a Jellyseerr extension; vanilla Overseerr ignores it and returns an unrelated page.
- **qBittorrent WebUI API**: login success can be `204` + empty body (newer versions) as well as the commonly-documented `200` + `"Ok."` body (older versions) — check both. Torrent state strings include `error` and `missingFiles`, not just the commonly-referenced stalled states.
- **Radarr/Sonarr API**: the queue endpoint filters on `movieIds`/`seriesIds` — plural, array-bound. The singular `movieId`/`seriesId` is silently ignored and returns the whole instance's queue.
- **pydantic-settings**: for complex-typed fields (`list`/`set`/`dict`/`tuple`), the raw env string is JSON-decoded *before* any validator runs. A bare number is valid JSON and silently coerces, bypassing string-based validators. Use `Annotated[T, NoDecode]` to opt out when the field needs custom string parsing.

Related: [[feedback-spec-harden-and-final-review]] (a different failure mode — internal logic/intent correctness, not external library behavior — but the same "the builder ships the spec's assumption literally" root cause).
