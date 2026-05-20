# Contributing

Thanks for considering a contribution to cleanrr. The project is small and the standards are intentionally simple.

## Development setup

```bash
git clone https://github.com/RayanAlyasi/cleanrr.git
cd cleanrr
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install
```

This installs the package in editable mode plus the dev dependencies (ruff, pyright, pytest, pre-commit), and wires the git hook that runs ruff before each commit.

## Running the bot locally

You'll need a `.env` (copy from `.env.example`) with a `TELEGRAM_BOT_TOKEN` and one of `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`. Then:

```bash
python -m cleanrr
```

## Quality bar

Every PR must pass:

- **`ruff check .`** — linter
- **`ruff format --check .`** — formatter (use `ruff format .` to fix)
- **`pyright`** — type checker
- **`bandit -r cleanrr/ -ll`** — security smells
- **`pytest`** — tests (includes `tests/test_consistency.py` which checks docs↔code drift)
- **`jscpd`** — duplicate-code detection (runs in CI)

CI runs all of these on every PR. `pre-commit install` runs the fast ones locally before each commit so they rarely fail in CI.

## Code style

- Type hints everywhere — pyright runs in strict-ish mode.
- Default to **no comments**. Only write a comment when the *why* is non-obvious (a workaround, a subtle invariant, a constraint). Names should already explain *what*.
- Keep modules focused. If `bot.py` grows past ~150 lines, split handlers into their own module.
- Configurability lives in `.env` / `Settings`, not in hardcoded constants. Anything a user might reasonably want to change should be a setting.
- Don't add features, abstractions, or error handling for hypothetical future requirements.

## Submitting a PR

1. Open an issue first for anything non-trivial — saves wasted work if the direction isn't right.
2. Branch from `main`.
3. Keep PRs focused: one logical change per PR.
4. Use [Conventional Commits](https://www.conventionalcommits.org/) — `feat:`, `fix:`, `chore:`, `docs:`, `ci:`, `refactor:`, `test:`. release-please reads these on every push to main and opens a Release PR when there is a user-visible change.
5. **Do not edit `CHANGELOG.md` by hand** — release-please regenerates it. Manual edits get overwritten.
6. Make sure CI is green before requesting review.

## How we work with Claude Code

cleanrr is set up to be developed with the [Claude Code](https://code.claude.com/) CLI using a three-tier workflow. Contributors using Claude Code automatically inherit the same standards.

**Three tiers:**

| Tier | Model | Role |
| --- | --- | --- |
| Planning | Opus | Deciding architecture, writing specs, integrating findings. Stays in the main session. |
| Orchestration | Haiku | Running the `/cleanrr-ship` and `/cleanrr-audit` slash commands. Cheap macro layer. |
| Execution | Sonnet | The three subagents — `cleanrr-builder`, `cleanrr-reviewer`, `cleanrr-security`. Do the actual work. |

**The agents:**

- `cleanrr-builder` (Sonnet) — implements from a spec, writes tests first, runs local checks until green
- `cleanrr-reviewer` (Sonnet, read-only) — audits style, naming, comment hygiene, and *docs↔code coherence* (README mentions match code, `.env.example` matches `Settings`, etc.)
- `cleanrr-security` (Sonnet, read-only + bandit) — audits secrets handling, SQL parameterisation, admin gates, untrusted-input boundaries

**The slash commands:**

- `/cleanrr-ship <spec>` — branch → builder → consistency test → reviewer → (security if relevant) → PR. Fail-closed gates between each step.
- `/cleanrr-audit` — whole-project review + security sweep. Periodic health check, not per-PR.

**Why this matters (the harness rationale):**

We deliberately keep constraints in the harness (tool allowlists, path-scoped rules under `.claude/rules/`, `permissionMode: plan` for review agents) rather than in long prompts. The reviewer literally cannot edit files because `Write` and `Edit` are not in its `allowedTools`. The security agent's `Bash` is restricted to `bandit` only. Constraints as code, not as please-don't.

You don't need Claude Code to contribute — these are tools, not requirements. The CI checks and `pytest` coverage are the actual enforcement layer.

## Reporting bugs

Open an issue with:
- What you expected to happen
- What actually happened
- Steps to reproduce (or your `.env` minus secrets, the relevant log lines, and the bot phase you're on)
- Your environment: OS, Python version, Docker version

## Reporting security issues

Please **don't** open a public issue for security vulnerabilities. Email the maintainer directly. (Until a `SECURITY.md` lands, use the email on the project's GitHub profile.)
