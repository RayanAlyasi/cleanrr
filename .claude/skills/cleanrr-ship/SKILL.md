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
- Read the plan. If `Status:` is `needs-user-decision`, stop and show the open question from its **Decisions** section; the owner answers it in the plan and sets `Status: ready`. Do not choose for them.
- Extract `Branch:`, `PR title:`, the waves, and every task spec. If a wave has two tasks naming the same file, stop and send the plan back to `/cleanrr-plan`; do not fix it yourself.
- `git checkout -b <Branch>`.

## 2. Waves

For each wave in order:

1. **Spawn every task in the wave in one message**, one `Agent` call per task with `subagent_type: cleanrr-coder`. Each prompt is the full task spec verbatim, followed by: the plan path; the feature branch name; "You are in an isolated worktree branched from that branch. Commit with `git commit -s` when the gate is green, then report your branch name, worktree path, `git diff --stat HEAD~1`, and the gate output." Coders in the same wave run in parallel; do not spawn wave N+1 until every coder in wave N has returned.
2. **If a coder stopped at its turn limit without reporting,** look before you resume it: `git -C <worktree> status --short` and `bash .claude/hooks/gate.sh` run from that worktree. If the diff matches the spec and the gate is green, commit it there with `git commit -s` and carry on; otherwise send it a message to continue. A review agent that stops without a report is resumed with a message asking for the report from what it has already read.
3. **Integrate in task order, one clean commit per task.** For each coder report: `git merge --squash <coder-branch>` then `git commit -s -m "<the task's commit subject>"`. This keeps the branch linear and keeps worktree branch names out of the history the squash merge will list. On a conflict, `git merge --abort` (or `git reset --merge`), stop, and report which two tasks collided; the plan's file ownership was wrong.
4. **Gate the integrated branch:** `bash .claude/hooks/gate.sh`. On failure, spawn one `cleanrr-coder` with a fix spec whose Goal is "make the gate green", whose Files are the files named in the failure output, and whose Steps quote the failure verbatim. Merge and re-gate. Two attempts per wave, then stop and report.
5. **Clean up:** for each integrated coder, `git worktree remove --force <worktree-path>` then `git branch -D <coder-branch>` (a squash leaves no ancestry, so `-d` would refuse); finally `git worktree prune`.

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

Write the title and body to `.claude/rules/commit-style.md`: what was wrong or missing, what changes, and what a reviewer cannot see in the diff. The PR must read as a maintainer's own description of the change. It never mentions the plan, waves, tasks, agents, review verdicts, or gate output; those go in your report to the user, not in the repository's history. Never merge; never use `--admin`.

## 5. Report

```
PR: <url>
Branch: <name>   Waves: <n>   Tasks: <n>   Fix rounds: <n>
Gate: green
Reviewer: <verdict>   Security: <verdict>
Deferred: <optional/low items left for the user, or none>
```
