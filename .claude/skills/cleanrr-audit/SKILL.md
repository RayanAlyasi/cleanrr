---
name: cleanrr-audit
description: Whole-repository sweep — cleanrr-reviewer and cleanrr-security against the entire tree, not a diff, consolidated into one severity-ordered punch list that /cleanrr-plan can consume. Periodic health check, not per-PR.
disable-model-invocation: true
allowed-tools: Agent, Read, Glob, Grep, Bash(git:*), Bash(bash .claude/hooks/gate.sh:*)
---

Run a whole-project audit. Do not edit any file.

## 1. Baseline the tree

`git status --porcelain` and `bash .claude/hooks/gate.sh`. A red gate is the first finding; still run the agents.

## 2. Spawn both agents in one message

- `cleanrr-reviewer`: "Audit the entire `cleanrr/` and `tests/` trees plus README.md, CONTRIBUTING.md, ARCHITECTURE.md, .env.example. There is no diff; treat every file as in scope. Apply your full checklist, with extra weight on duplication across `cleanrr/tools/` and on coherence between docs and code."
- `cleanrr-security`: "Audit the entire repository: `cleanrr/`, `.github/`, `Dockerfile`, `docker-compose.yml`, `pyproject.toml`, and every root Markdown file. Apply both lanes: the application checklist and the full OpenSSF Baseline mapping, reporting every maturity-1 and maturity-2 control's status."

## 3. Consolidate

Merge both reports into one list ordered Critical/Blockers, High, Medium, Coherence, Baseline, Verify, Low/Optional. Prefix each entry with `[reviewer]` or `[security]`. Collapse duplicates. Any `## Verify` item the security agent could not resolve stays listed as open, not dropped.

## 4. Output

```
## cleanrr audit — <date>
### Gate
### Critical / Blockers
### High
### Medium
### Coherence
### Baseline (OSPS control → status)
### Open Verify items
### Low / Optional
### Summary
<verdict, and the three findings to plan first — run `/cleanrr-plan` with this report as input>
```
