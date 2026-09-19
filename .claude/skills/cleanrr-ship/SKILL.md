---
name: cleanrr-ship
description: Execute a cleanrr plan end to end — one cleanrr-coder per task in parallel worktrees per wave, merge, quality gate, then cleanrr-reviewer and cleanrr-security in parallel, a bounded fix loop, and a pull request. You run this after reviewing the plan /cleanrr-plan wrote.
argument-hint: "[.claude/plans/<slug>.md | light: <small request>]"
disable-model-invocation: true
allowed-tools: Agent, SendMessage, Read, Write, Glob, Grep, Bash(git:*), Bash(gh:*), Bash(bash .claude/hooks/gate.sh:*), Bash(python .claude/hooks/lane.py:*), Bash(python .claude/hooks/plan_lint.py:*), Bash(python .claude/hooks/review_pack.py:*), Bash(python .claude/hooks/usage_report.py:*)
---

You orchestrate one plan through to a pull request. You delegate every code change to `cleanrr-coder` and every judgment call about the result to `cleanrr-reviewer` and `cleanrr-security`. You never edit `cleanrr/`, `tests/`, or `.github/` yourself. Fail closed: when a gate fails and the bounded retries are spent, stop and report; do not push a red branch.

**Run this from a fresh session, or after `/clear`.** Every turn re-reads the whole conversation, and the first run spent as much on an orchestrator carrying unrelated history as on all its agents together. The plan file is the hand-off; nothing else is needed.

Input: `$ARGUMENTS`. A path is a plan file (if empty, use the newest file in `.claude/plans/`). `light: <request>` asks for the light lane.

## 1. Preconditions and lane

The reviewer and the security agent run on every change. The lane only decides whether a plan is required.

- **Full lane** (any plan file): everything below.
- **Light lane** (`light: <request>`): for a docs-only change or one small source file. Name the files you expect to touch and run `python .claude/hooks/lane.py --paths <files>`. If it prints `full`, stop and tell the user to run `/cleanrr-plan`; do not argue with the script. If it prints `light`, write a one-task plan yourself to `.claude/plans/<slug>.md` with the planner's task headings (Goal, Files, Out of scope, Existing code to follow, Steps, Tests, Verification, Invariants, Docs and compliance, Commit subject) and continue as a one-wave plan. After the coder returns, run `python .claude/hooks/lane.py` on the diff; if the change outgrew the light lane, stop and report it rather than reviewing a change nobody planned.

- `git status --porcelain` is empty apart from ignored paths, and you are on `main` with `git pull --rebase origin main` clean. Otherwise stop.
- Read the plan. If `Status:` is `needs-user-decision`, stop and show the open question from its **Decisions** section; the owner answers it in the plan and sets `Status: ready`. Do not choose for them.
- `python .claude/hooks/plan_lint.py <plan>` exits 0. On any problem, stop and send the plan back to `/cleanrr-plan` with the output; do not fix it yourself.
- Extract `Branch:`, `PR title:`, the waves, and every task spec. If a wave has two tasks naming the same file, stop and send the plan back to `/cleanrr-plan`; do not fix it yourself.
- `git checkout -b <Branch>`.

## 2. Waves

For each wave in order:

1. **Spawn every task in the wave in one message**, one `Agent` call per task with `subagent_type: cleanrr-coder`. Each prompt names the plan by absolute path and the task's heading and line range, and tells the coder to read its spec from there (`.claude/plans/` is gitignored, so a worktree does not contain the plan, and pasting specs grows your own context). Follow that with: the plan path; the feature branch name; "You are in an isolated worktree branched from that branch. Commit with `git commit -s` when the gate is green, then report your branch name, worktree path, `git diff --stat HEAD~1`, and the gate output." Coders in the same wave run in parallel; do not spawn wave N+1 until every coder in wave N has returned.
2. **If a coder stopped at its turn limit without reporting,** look before you resume it: `git -C <worktree> status --short` and `bash .claude/hooks/gate.sh </dev/null` run from that worktree. If the diff matches the spec and the gate is green, commit it there with `git commit -s` and carry on; otherwise send it a message to continue. A review agent that stops without a report is resumed with a message asking for the report from what it has already read. Wait for an agent's completion notice before you message it: a message sent while it is still finishing is queued and can arrive twice. An agent reported as `failed` by a usage limit may still have finished: check for its artifact on disk (the plan file, the worktree commit) before re-running it.
3. **Integrate in task order, one clean commit per task.** For each coder report: `git merge --squash -q <coder-branch>` then `git commit -q -s -m "<the task's commit subject>" >/dev/null` (the pre-commit hooks print about fifteen lines per commit that you would otherwise re-read on every later turn; a non-zero exit still tells you a hook failed). This keeps the branch linear and keeps worktree branch names out of the history the squash merge will list. On a conflict, `git merge --abort` (or `git reset --merge`), stop, and report which two tasks collided; the plan's file ownership was wrong.
4. **Gate the integrated branch:** `bash .claude/hooks/gate.sh </dev/null` (the Bash tool leaves stdin open, and the gate waits on it). On failure, spawn one `cleanrr-coder` with a fix spec whose Goal is "make the gate green", whose Files are the files named in the failure output, and whose Steps quote the failure verbatim. Merge and re-gate. Two attempts per wave, then stop and report.
5. **Clean up:** for each integrated coder, `git worktree remove --force <worktree-path>` then `git branch -D <coder-branch>` (a squash leaves no ancestry, so `-d` would refuse); finally `git worktree prune`.

## 3. Review

Save the final gate output to a file and build the review pack: `python .claude/hooks/review_pack.py --plan <plan> --gate <gate-output-file>`. Then spawn **both in one message**: `cleanrr-reviewer` and `cleanrr-security`, each with the feature branch name, the plan path, and the review pack path, told to read the pack first and to diff against `main` for anything it does not show.

- Any `## Blockers`, `## Critical`, or `## High`, or a `BLOCK` / `NEEDS REVISION` verdict: assemble one fix spec per owning file set from the findings (file, issue, failure scenario, suggested fix, verbatim), spawn coders in one message, merge, gate, then re-review. Two rounds, then stop and report.
- **Every value in a fix spec cites its source.** A constant, bound, or condition you write into a fix spec carries the `file:line` (in cleanrr or in the installed library) that justifies it. If you cannot cite it, verify it first; an unverified number in a fix spec buys a whole extra review round.
- **Re-review by resuming, not respawning.** Send the agent that flagged a message naming the fix commits (`git show <sha>` for each) and asking whether each of its findings is closed and whether those commits introduced anything new. It already holds the branch in context, so this costs a few turns instead of a full review. Spawn a fresh agent only when the diff changed shape or the first one is past about 200k tokens of context.
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

Then run `python .claude/hooks/usage_report.py --session ${CLAUDE_SESSION_ID}` and include its table. Cost follows context size times turns, so an agent with an unusual turn count or context is worth a look before the next run.
