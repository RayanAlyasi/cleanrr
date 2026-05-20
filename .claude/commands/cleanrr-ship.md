---
description: Orchestrate a feature end-to-end — build, review, security-check (if relevant), then open the PR.
argument-hint: "<spec or path-to-spec>"
model: haiku
allowed-tools:
  - Agent
  - Bash
  - Read
  - AskUserQuestion
permissionMode: plan
---

# /cleanrr-ship

You orchestrate a single feature shipping run. The user has already planned with Opus and gives you a spec. Your job is to chain `cleanrr-builder`, `cleanrr-reviewer`, and conditionally `cleanrr-security`, with fail-closed gates between them.

## Execution Contract (non-negotiable)

You MUST:
- Treat the user's spec as authoritative. If acceptance criteria are missing → STOP and ask via `AskUserQuestion`.
- Run steps in order. Do not skip ahead.
- At every gate, halt and surface findings if the prior step failed.
- Use the `Agent` tool to delegate work. Never edit code yourself.

You are forbidden from:
- Editing any file in `cleanrr/`, `tests/`, or `.github/`.
- Skipping the consistency-test gate.
- Skipping the Opus final review.
- Opening a PR before all required agents have approved.

## Workflow

### Step 1 — Parse spec, create branch
Read the spec. Extract: branch name (`feat|fix|chore/<slug>`), commit-message subject, files expected to change.

```
git checkout main && git pull --rebase origin main
git checkout -b <branch>
```

If anything ambiguous → STOP, use `AskUserQuestion`.

### Step 2 — Build
```
Agent(subagent_type="cleanrr-builder", description="Build per spec",
      prompt="<full self-contained spec including file paths, acceptance criteria, and any cleanrr conventions the agent must follow>")
```

If the builder reports failure or missing inputs → STOP and surface to the user.

### Step 3 — Deterministic consistency gate
```
python -m pytest tests/test_consistency.py -v
```

If this fails, the diff drifted docs↔code or env↔Settings. STOP and surface the failure.

### Step 4 — Style and coherence review
```
Agent(subagent_type="cleanrr-reviewer", description="Audit diff for style/coherence",
      prompt="Review the diff on branch <branch>. Files touched: <list>. Apply the cleanrr-reviewer checklist.")
```

If verdict is `NEEDS REVISION` or any `## Blockers` exist → STOP and surface.

### Step 5 — Security review (conditional)
Run `git diff --name-only main...HEAD` and check if any path matches:
- `cleanrr/config.py`
- `cleanrr/identity.py`
- `cleanrr/tools/`
- `cleanrr/bot.py` (if the diff added or removed a command)
- Any new file under `cleanrr/`

If yes:
```
Agent(subagent_type="cleanrr-security", description="Security audit",
      prompt="Audit the diff on branch <branch>. Files touched: <list>. Apply the cleanrr-security checklist.")
```

If verdict is `BLOCK RELEASE` or any `## Critical` finding → STOP and surface.

### Step 6 — Opus final review (intent vs literal)

The specialized agents check their lanes (style, security). Opus checks whether the code does what the spec *meant*, not just what it said. This catches correctness bugs that survive a literal spec interpretation — gauge drift on upserts, missing instrumentation across symmetric paths, error-path observability gaps.

```
Agent(subagent_type="claude", model="opus", description="Final intent review",
      prompt="Final correctness review of branch <branch>. Run `git diff main...HEAD` to see the change. Ask: does the code do what the spec MEANT, not just what it said? Look for counter/gauge drift, missing instrumentation across symmetric paths, error paths that skip observability the user would want, and any place where the spec's literal instruction doesn't achieve its stated goal. Do NOT re-flag style, naming, README drift, or security — the other agents covered those. Report `## Blockers`, `## Worth fixing`, `## Considered and dismissed`, `## Verdict`. Under 400 words.")
```

If verdict is `NEEDS REVISION` or any `## Blockers` exist → STOP and surface to the user. The user decides whether to apply fixes on this branch (recommended) or defer.

### Step 7 — Push and open PR
```
git push -u origin <branch>
gh pr create --title "<conventional-commit subject>" --body "<spec summary + builder report + reviewer + security verdicts>"
```

## Output Summary

After the PR is open, return:

```
PR: <url>
Builder: <files changed, test count>
Consistency: PASS
Reviewer: <verdict>
Security: <verdict or "not triggered">
Opus final: <verdict>
```
