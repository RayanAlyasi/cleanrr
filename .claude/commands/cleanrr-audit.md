---
description: Whole-project sweep — cleanrr-reviewer plus cleanrr-security against the entire codebase (not just diff). Returns a consolidated punch list.
model: haiku
allowed-tools:
  - Agent
  - Bash
permissionMode: plan
---

# /cleanrr-audit

Periodic health check. Runs both review agents against the entire cleanrr codebase.

## Execution Contract (non-negotiable)

You MUST:
- Run both agents in parallel (single message with two `Agent` calls).
- Aggregate findings into one report, severity-ordered.
- Do not edit any file.

You are forbidden from:
- Skipping either agent.
- Treating "no diff" as a reason to skip — the whole point is sweeping committed code.

## Workflow

### Step 1 — Spawn both reviews in parallel
```
Agent(subagent_type="cleanrr-reviewer", description="Whole-project review",
      prompt="Audit the entire cleanrr/ tree and tests/ tree. Scope: every Python file plus README, CONTRIBUTING, .env.example.")

Agent(subagent_type="cleanrr-security", description="Whole-project security audit",
      prompt="Audit the entire cleanrr/ tree. Run bandit. Apply the full security checklist.")
```

### Step 2 — Consolidate

Merge both reports. Order: Blockers + Critical first, then High, then Medium, then Suggestions. Cite the source agent for each finding.

## Output Format

```
## Cleanrr Audit — <date>

### Critical / Blockers
- [security] file:line — ...
- [reviewer] file:line — ...

### High
- ...

### Coherence Findings
- [reviewer] ...

### Suggestions
- ...

### Summary
<one paragraph: overall verdict, recommended next actions>
```
