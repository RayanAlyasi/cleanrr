# Commit and PR style — cleanrr

## Commits

- Conventional Commits. Subject prefix is one of: `feat:`, `fix:`, `perf:`, `deps:`, `docs:`, `chore:`, `ci:`, `refactor:`, `test:`, `build:`.
- Subject ≤ 50 characters, imperative mood, no trailing period.
- Body only when the *why* is non-obvious (security CVE references, surprising tradeoffs, upstream workarounds). Wrap body lines at 72.
- Never restate the diff in prose. If the body just enumerates what the diff shows, delete it.
- Every commit is signed off (`git commit -s`). No other trailers: no `Co-Authored-By:`, no generated-by lines, no session links.
- `feat:` and `fix:` show up in CHANGELOG and trigger version bumps (minor / patch). `chore:` and friends are hidden from the changelog — use deliberately if you want a change recorded but not released.

## Pull requests

- The title is the Conventional Commit subject the squash merge will use.
- The body is for a maintainer reading the history a year from now: what was wrong or missing, what changes, and anything a reviewer cannot see in the diff (a behaviour change, a migration step, a follow-up deliberately left out). Two to six lines is normal. Bullets only for genuinely parallel changes. No `## Summary` / `## Test plan` scaffolding on a small change.
- Mention testing only when it went beyond CI: a check against a live service, a manual repro, a release build. Never list ruff, pyright, or pytest results; the checks tab already shows them.
- `Fixes #N` on its own line when an issue exists.

## Voice

Write as the maintainer describing a change, in plain present tense. The message is about the code, never about how the change came to be.

- No reference to a request, a conversation, a session, feedback, or a person asking ("as requested", "per discussion", "reported in chat").
- No account of the process or the effort ("after investigating", "traced it to", "verified by hand", "this took real investigation", "found live tonight"). State the symptom, the cause, and the fix.
- No reassurance or self-justification ("not assumed", "carefully", "properly", "actually", "nothing was changed without…").
- No filler adjectives: comprehensive, robust, seamless, enhanced, streamlined, leverage.
- No first person, no "This PR…" / "This commit…" openers, no emoji, no exclamation marks.
- No mention of agents, plans, review verdicts, or any tooling that produced the change.

The register to match:

> Every release built, pushed, and signed the image twice. release-please creates the tag with a PAT, so the tag creation fires `push: tags` in addition to `release: published`.
>
> Dropping the `push: tags` trigger leaves `release: published` for release-please and `workflow_dispatch` for manual rebuilds.
