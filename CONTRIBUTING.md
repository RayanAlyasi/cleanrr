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
- **`pytest`** — tests

CI runs all four on every PR. `pre-commit install` runs the first two automatically before each commit so they rarely fail in CI.

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
4. Update `CHANGELOG.md` under the "Unreleased" section.
5. Make sure CI is green before requesting review.

## Reporting bugs

Open an issue with:
- What you expected to happen
- What actually happened
- Steps to reproduce (or your `.env` minus secrets, the relevant log lines, and the bot phase you're on)
- Your environment: OS, Python version, Docker version

## Reporting security issues

Please **don't** open a public issue for security vulnerabilities. Email the maintainer directly. (Until a `SECURITY.md` lands, use the email on the project's GitHub profile.)
