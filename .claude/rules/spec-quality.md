# Spec quality — cleanrr

Before invoking `/cleanrr-ship`, run a correctness pass on the spec against the surrounding code. `cleanrr-builder` follows the spec literally — any logic error in the spec ships as a logic error in the code, with reviewer and security blind to it (different lanes).

For every line of code the spec dictates, ask: **does it actually do what its name claims, given the rest of the codebase?** Pay attention to:

- **Counter vs gauge.** `Counter.inc()` is for monotonic events. `Gauge.set()` is for current state. Check upserts — `ON CONFLICT DO UPDATE` doesn't grow the table, so `inc()` will drift above truth on re-link / re-submit / retry.
- **Symmetric paths.** If you label by `command`, instrument *every* command (`/start`, `/help`, `/invite`, `/link`, ...). The label only earns its keep if every path sets it. Same logic for any per-handler counter/log.
- **Error paths.** Decide deliberately whether each metric/log fires on success only, error only, or both. Latency-on-success is defensible; error-count without success-count is not.
- **Idempotency.** If a handler can be called twice (retry, reconnect, replay), does the spec's mutation hold? Re-runs should converge, not drift.
- **Conditional work.** If a feature is opt-in (`*_ENABLED=false` by default), gate the work behind the flag. Don't run DB queries or background tasks for nobody.

The downstream agents (builder, reviewer, security) are each scoped to their lane and won't deviate from a flawed spec. Catching it here is the highest-leverage move. `/cleanrr-ship` runs an Opus final review as a backstop, but that's a backstop — not a substitute for spec discipline.
