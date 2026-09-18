---
name: cleanrr-plan
description: Turn a feature request, bug, or audit punch list into a plan of small, parallelizable task specs at .claude/plans/<slug>.md, ready for /cleanrr-ship. Runs the cleanrr-planner agent (Opus) in a forked context.
argument-hint: "<what to build or fix>"
disable-model-invocation: true
context: fork
agent: cleanrr-planner
background: false
---

Plan the following change to cleanrr:

$ARGUMENTS

Follow your planning contract: explore the real code first, verify every library or API assumption against fetched docs or installed source, split the work into waves of tasks with disjoint file sets, and write the plan to `.claude/plans/<slug>.md`. If the request references an audit or review report, treat each finding as a candidate task and drop the ones that don't survive reading the code.

Consult `cleanrr-architect` only for a structural fork your contract lists, never for routine planning.

Return only the plan path, the five-line summary, and the plan's `Status:`.
