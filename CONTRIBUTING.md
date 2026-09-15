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

Any PR that adds or changes functionality must add or update tests covering it in the same PR — coverage doesn't happen retroactively.

## Code style

[`CODE_STANDARDS.md`](CODE_STANDARDS.md) is the full reference; [`DESIGN_PRINCIPLES.md`](DESIGN_PRINCIPLES.md) explains what gets built and why. The short version:

- Type hints everywhere — pyright runs in strict mode.
- Default to **no comments**. Only write a comment when the *why* is non-obvious (a workaround, a subtle invariant, a constraint). Names should already explain *what*.
- Keep modules focused. Past ~150 lines, split.
- Configurability lives in `.env` / `Settings`, not in hardcoded constants. Anything a user might reasonably want to change should be a setting.
- Don't add features, abstractions, or error handling for hypothetical future requirements.

## Dependency management

Direct runtime dependencies are declared in `pyproject.toml` with version ranges, chosen from actively-maintained, widely-used PyPI packages. GitHub Actions used in CI/CD are pinned to an exact commit SHA rather than a mutable tag.

Dependabot tracks updates for the `pip` and `github-actions` ecosystems, plus the Docker base image, on a weekly schedule with a 7-day cooldown — a newly-published version has to sit for a week before Dependabot proposes it, so a just-published (and potentially compromised) release doesn't get pulled in immediately. Updates land as normal PRs and go through the same CI gate (tests, lint, security scans) as any other change.

## Submitting a PR

1. Open an issue first for anything non-trivial — saves wasted work if the direction isn't right.
2. Branch from `main`.
3. Keep PRs focused: one logical change per PR.
4. Use [Conventional Commits](https://www.conventionalcommits.org/) — `feat:`, `fix:`, `chore:`, `docs:`, `ci:`, `refactor:`, `test:`. release-please reads these on every push to main and opens a Release PR when there is a user-visible change.
5. Sign off every commit (`git commit -s`) — certifies you're legally authorized to submit the contribution under the project's license ([DCO](https://developercertificate.org/)). Enforced in CI.
6. **Do not edit `CHANGELOG.md` by hand** — release-please regenerates it. Manual edits get overwritten.
7. Make sure CI is green before requesting review.

## How we work with Claude Code

cleanrr is developed with the [Claude Code](https://code.claude.com/) CLI. The harness under `.claude/` is committed, so contributors using Claude Code inherit the same agents, gates, and rules. You don't need it to contribute: CI and `bash .claude/hooks/gate.sh` are the actual enforcement layer.

**Pipeline**

| Step | Skill → agent | Model | What it does |
| --- | --- | --- | --- |
| Plan | `/cleanrr-plan <request>` → `cleanrr-planner` | Opus | Reads the real code, verifies library/API assumptions against fetched docs, and writes `.claude/plans/<slug>.md`: small task specs grouped into parallel waves with disjoint file sets. |
| Build | `/cleanrr-ship <plan>` → `cleanrr-coder` × N | Sonnet | One coder per task, each in its own git worktree, all tasks of a wave in parallel. A Stop hook runs the gate, so a coder cannot finish red. The orchestrator merges each wave, re-gates, and cleans up. |
| Review | `cleanrr-reviewer` + `cleanrr-security`, in parallel | Opus | Correctness, intent-vs-literal, duplication, test gaps, docs↔code coherence; the application trust boundary plus an OpenSSF Baseline regression check. Both are read-only; findings feed a bounded fix loop before the PR opens. |
| Audit | `/cleanrr-audit` | Opus | Whole-repository sweep with both review agents; the output is a punch list `/cleanrr-plan` can consume. |

**Constraints as code, not as please-don't**

- `.claude/hooks/gate.sh` runs ruff, ruff format, pyright, bandit, and pytest with the project's interpreter (it finds the main checkout's `.venv` from inside a worktree). Exit 2 blocks the coder's Stop hook.
- `.claude/hooks/protect-paths.sh` is a PreToolUse guard: coders can't touch release-please-owned files, the planner writes only plans, and the review agents write only their own memory directory.
- Agent frontmatter allowlists do the rest: coders have no web access (the planner already verified the facts they need), review agents can't spawn agents, the planner can't `Edit`.
- `.claude/rules/` holds path-scoped rules every agent loads automatically; `.claude/skills/openssf-baseline/` maps each Baseline control to the file that satisfies it and the diff that would regress it.
- `worktree.baseRef` is `head` in `.claude/settings.json`, so coder worktrees branch from the feature branch the orchestrator is on, not from `main`.

## Reporting bugs

Open an issue with:
- What you expected to happen
- What actually happened
- Steps to reproduce (or your `.env` minus secrets, the relevant log lines, and the bot phase you're on)
- Your environment: OS, Python version, Docker version

## Reporting security issues

See [SECURITY.md](SECURITY.md) — please don't open a public issue for security vulnerabilities.
