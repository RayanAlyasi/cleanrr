# Design principles

How decisions get made in cleanrr. [`CODE_STANDARDS.md`](CODE_STANDARDS.md) covers how code is written; this covers what gets built and why. The product boundary lives in [`README.md`](README.md) ("Why this exists", "Out of scope") and the security posture in [`THREAT_MODEL.md`](THREAT_MODEL.md).

## What cleanrr is, and is not

cleanrr is the conversational layer for the residual media-request problems that Overseerr, Maintainerr, Decluttarr, and a well-configured *arr stack don't already solve. A friend asks the bot; the bot diagnoses; if a fix exists, the bot does it after the friend confirms.

It is not an Overseerr replacement, not a general-purpose chat bot, not a hosted service for strangers, and not a plugin host for arbitrary automation. When a proposal would make it one of those, the answer is no, however easy the change looks.

## Principles

### Diagnose, don't mirror

A tool earns its place by telling the user something the Overseerr UI can't. "Your request is approved" is a mirror. "It downloaded but Sonarr couldn't import it because the file already exists" is a diagnosis. Prefer surfacing the upstream reason (queue status messages, torrent state, tracker errors) over adding another status lookup.

### Deterministic controls, never prompt promises

The system prompt shapes tone and tool choice. It is not a security control. Anything that must be true (only the owner can cancel a request, only an admin can delete files, nothing destructive runs without a button tap) is enforced in code that Claude cannot bypass: `can_use_tool`, the tool-layer ownership recheck, `tools=[]`. If a design names a prompt line as the mitigation for a real risk, the design is not finished.

### Scope by construction

Every tool is built per user and closes over that user's Telegram id. Ownership is checked against what Overseerr says, not what Claude passes. There is no process-wide "current user". A new tool that needs the caller's identity gets it from its factory, never from a global.

### Honest failure over helpful guessing

A tool returns what it found, with `is_error` when it failed, and never invents a status to be reassuring. Its output carries the identifiers the next step needs. The bot says "I can't check that yet" rather than approximating.

### One small server, one admin

cleanrr runs on an old PC next to the media stack, for a handful of friends, administered by one person in their spare time. Every design is sized for that: bounded caps on subprocesses and pending confirmations, no background work when nobody has asked for anything, no paid SaaS where a local call works, no component that needs its own operator. An elegant architecture that needs a full-time maintainer is the wrong one here.

### Verify library behaviour, then build

Ten of the first bugs that shipped were "the docs say X, the library does Y". Any line that depends on how a library, SDK, or external API behaves is verified against current docs or installed source before it is written down. `.claude/rules/doc-verification.md` keeps the list of traps already hit.

### Configuration over constants; secrets are secrets

Anything an operator might reasonably change is a `Settings` field with a row in `.env.example` and README. Every token is a `SecretStr`. Internals (lock timeouts, retry counts) stay constants.

### Ship small, ship green

One pull request is one change. Every commit is a Conventional Commit with a DCO sign-off; release-please turns them into versions and a changelog. The gate (ruff, pyright strict, bandit, pytest) is green before review, not after.

## Anti-patterns

- **Duplicating the stack.** Notifications Overseerr already sends, cleanup Maintainerr already does, stalled-torrent handling Decluttarr already does. Integrate or defer; don't reimplement.
- **Abstraction for hypothetical futures.** A provider interface for models nobody has asked for, a plugin system for tools that don't exist. The multi-provider decision is recorded in README "Out of scope"; don't relitigate it in code.
- **Prompt-only safety.** See above. It is the most tempting shortcut and the least durable.
- **Unbounded fan-out.** N+1 fetches over a request list, fetching every torrent to find one, per-message subprocess spawns. Measure on the small server before assuming it's fine.
- **Polish before product.** Governance, badges, and CI hardening have real value, and cleanrr has most of them. When the choice is between another badge and a feature that changes what the bot can tell a user, the feature wins.
- **Breaking config or storage without a path.** Renaming a `Settings` field or changing the SQLite schema needs a migration or a compatibility shim and a changelog entry.

## Decision framework

When evaluating a change, in this order:

1. **Does it fit the boundary?** Residual media-request problems, for friends of one homelab, via Telegram. If not, stop.
2. **Does the user learn or achieve something new?** Compared with opening Overseerr. If not, it's a mirror.
3. **Where is the safety line?** Name the deterministic control that bounds the worst case. If there isn't one, design it first.
4. **What does it cost the server and the admin?** Memory, subprocesses, new credentials, new failure modes to debug at 11 pm.
5. **What already does most of this?** In the codebase (`cleanrr/tools/_*.py`, `permissions/`), in the stack, or in a library already installed.

Record the outcome where the next person will look: the PR body for the reasoning, README "Out of scope" for a declined direction, `THREAT_MODEL.md` for a new trust boundary, `ARCHITECTURE.md` for a new actor or interface.
