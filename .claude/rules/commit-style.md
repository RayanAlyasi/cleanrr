# Commit style — cleanrr

- Conventional Commits. Subject prefix is one of: `feat:`, `fix:`, `perf:`, `deps:`, `docs:`, `chore:`, `ci:`, `refactor:`, `test:`, `build:`.
- Subject ≤ 50 characters, imperative mood, no trailing period.
- Body only when the *why* is non-obvious (security CVE references, surprising tradeoffs, upstream workarounds). Wrap body lines at 72.
- Never restate the diff in prose. If the body just enumerates what the diff shows, delete it.
- **No `Co-Authored-By:` trailers.** Commits read as the user's own authorship.
- `feat:` and `fix:` show up in CHANGELOG and trigger version bumps (minor / patch). `chore:` and friends are hidden from the changelog — use deliberately if you want a change recorded but not released.
