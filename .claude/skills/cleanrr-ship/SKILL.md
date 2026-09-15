---
name: cleanrr-ship
description: Execute a cleanrr plan end to end — one cleanrr-coder per task in parallel worktrees per wave, merge, quality gate, then cleanrr-reviewer and cleanrr-security in parallel, a bounded fix loop, and a pull request. You run this after reviewing the plan /cleanrr-plan wrote.
argument-hint: "[.claude/plans/<slug>.md]"
disable-model-invocation: true
allowed-tools: Agent, Read, Glob, Grep, Bash(git:*), Bash(gh:*), Bash(bash .claude/hooks/gate.sh:*)
---

You orchestrate one plan through to a pull request. You delegate every code change to `cleanrr-coder` and every judgment call about the result to `cleanrr-reviewer` and `cleanrr-security`. You never edit `cleanrr/`, `tests/`, or `.github/` yourself. Fail closed: when a gate fails and the bounded retries are spent, stop and report; do not push a red branch.

Plan file: `$ARGUMENTS` (if empty, use the newest file in `.claude/plans/`).

## 1. Preconditions

- `git status --porcelain` is empty apart from ignored paths, and you are on `main` with `git pull --rebase origin main` clean. Otherwise stop.
- Read the plan. Extract `Branch:`, `PR title:`, the waves, and every task spec. If a wave has two tasks naming the same file, stop and send the plan back to `/cleanrr-plan`; do not fix it yourself.
- `git checkout -b <Branch>`.

## 2. Waves

For each wave in order:

1. **Spawn every task in the wave in one message**, one `Agent` call per task with `subagent_type: cleanrr-coder`. Each prompt is the full task spec verbatim, followed by: the plan path; the feature branch name; "You are in an isolated worktree branched from that branch. Commit with `git commit -s` when the gate is green, then report your branch name, worktree path, `git diff --stat HEAD~1`, and the gate output." Coders in the same wave run in parallel; do not spawn wave N+1 until every coder in wave N has returned.
2. **Merge in task order.** For each coder report: `git merge --no-ff --no-edit <coder-branch>`. On a conflict, `git merge --abort`, stop, and report which two tasks collided; the plan's file ownership was wrong.
3. **Gate the integrated branch:** `bash .claude/hooks/gate.sh`. On failure, spawn one `cleanrr-coder` with a fix spec whose Goal is "make the gate green", whose Files are the files named in the failure output, and whose Steps quote the failure verbatim. Merge and re-gate. Two attempts per wave, then stop and report.
4. **Clean up:** for each merged coder, `git worktree remove --force <worktree-path>` then `git branch -d <coder-branch>`; finally `git worktree prune`.

## 3. Review

Spawn **both in one message**: `cleanrr-reviewer` and `cleanrr-security`, each with the feature branch name, the plan path, the plan's "Review focus" section, and the final gate output. Tell each to diff against `main`.

- Any `## Blockers`, `## Critical`, or `## High`, or a `BLOCK` / `NEEDS REVISION` verdict: assemble one fix spec per owning file set from the findings (file, issue, failure scenario, suggested fix, verbatim), spawn coders in one message, merge, gate, then re-run only the agent(s) that flagged. Two rounds, then stop and report.
- `## Verify` items from the reviewer: give them to `cleanrr-security` to resolve (it has web access). An unresolved Verify item is not approval.
- `## Coherence` and `## Baseline: needs update` findings are fixed in the same fix round; they are cheap and the badges depend on them.

## 4. Pull request

```
git push -u origin <Branch>
gh pr create --title "<PR title>" --body-file <tmp>
```

Body: the plan's Goal and Design in two short paragraphs; a list of tasks per wave; the reviewer and security verdicts with any accepted `## Optional` or `## Low` items named; the final gate summary line. No attribution trailers of any kind. Never merge; never use `--admin`.

## 5. Report

```
PR: <url>
Branch: <name>   Waves: <n>   Tasks: <n>   Fix rounds: <n>
Gate: green
Reviewer: <verdict>   Security: <verdict>
Deferred: <optional/low items left for the user, or none>
```
