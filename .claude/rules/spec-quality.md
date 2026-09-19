# Spec quality — cleanrr

`cleanrr-planner` runs this correctness pass on every task spec before writing a plan; anyone writing a plan by hand does the same. `cleanrr-coder` follows the spec literally — any logic error in the spec ships as a logic error in the code, and the reviewer sees it only if it happens to show in the diff.

For every line of code the spec dictates, ask: **does it actually do what its name claims, given the rest of the codebase?** Pay attention to:

- **Counter vs gauge.** `Counter.inc()` is for monotonic events. `Gauge.set()` is for current state. Check upserts — `ON CONFLICT DO UPDATE` doesn't grow the table, so `inc()` will drift above truth on re-link / re-submit / retry.
- **Symmetric paths.** If you label by `command`, instrument *every* command (`/start`, `/help`, `/invite`, `/link`, ...). The label only earns its keep if every path sets it. Same logic for any per-handler counter/log.
- **Error paths.** Decide deliberately whether each metric/log fires on success only, error only, or both. Latency-on-success is defensible; error-count without success-count is not.
- **Idempotency.** If a handler can be called twice (retry, reconnect, replay), does the spec's mutation hold? Re-runs should converge, not drift.
- **Text guards.** A test that guards a config or workflow file by matching text must fail on the nearest regressing edit, and the spec names that edit. `"-w /" in line` also passes for `-w /app`; asserting three literals is not a parity check between two steps. Compare parsed values, or the two things to each other.
- **Dependency sets come from imports.** For any change to what is declared, pinned, or audited, enumerate `import` statements under `cleanrr/` and `tests/` rather than trusting `pyproject.toml`; a package imported directly but installed only transitively is undeclared.
- **Conditional work.** If a feature is opt-in (`*_ENABLED=false` by default), gate the work behind the flag. Don't run DB queries or background tasks for nobody.

**External library/API behavior.** If a line of the spec depends on how a third-party library, SDK, or external API behaves — not just internal codebase logic — verify that behavior against actually-fetched current docs or source before finalizing the spec, and record it under the plan's "Verified assumptions". `cleanrr-coder` has no WebFetch/WebSearch access and implements verbatim; the planner and `cleanrr-security` are the only agents that can research. See `doc-verification.md` for the concrete gotcha list this project has already been burned by.

The downstream agents (coder, reviewer, security) are each scoped to their lane and won't deviate from a flawed spec. Catching it here is the highest-leverage move. `cleanrr-reviewer` runs on Opus and checks intent-vs-literal as a backstop, but that's a backstop — not a substitute for spec discipline.
