"""Deterministic drift checks between docs and code.

These run in CI and as part of /cleanrr-ship before invoking the reviewer agent —
they catch the cheap, mechanical mismatches so the LLM only spends tokens on the
judgement calls.
"""

from __future__ import annotations

import re
from pathlib import Path

from cleanrr.config import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = REPO_ROOT / ".env.example"
README = REPO_ROOT / "README.md"
BOT_PY = REPO_ROOT / "cleanrr" / "bot.py"

# Settings fields that intentionally have no .env.example entry because they are
# meant for Docker compose interpolation or local-dev escape hatches, not the
# bot's pydantic-settings layer.
_ENV_EXAMPLE_EXTRAS = {"DOCKER_NETWORK_NAME"}


def _env_example_vars() -> set[str]:
    vars_found: set[str] = set()
    for raw_line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Z][A-Z0-9_]*)=", line)
        if match:
            vars_found.add(match.group(1))
    return vars_found


def _settings_env_names() -> set[str]:
    return {name.upper() for name in Settings.model_fields}


def _registered_commands() -> set[str]:
    text = BOT_PY.read_text(encoding="utf-8")
    return set(re.findall(r'CommandHandler\(\s*"([a-z]+)"', text))


def _readme_commands() -> set[str]:
    text = README.read_text(encoding="utf-8")
    # Match rows inside the Commands table: | `/cmd <args>` | role | desc |
    return set(re.findall(r"\|\s*`/([a-z]+)(?:\s[^`]*)?`\s*\|", text))


def test_settings_fields_are_documented_in_env_example() -> None:
    documented = _env_example_vars()
    expected = _settings_env_names()
    missing = expected - documented
    assert not missing, f"Settings fields missing from .env.example: {sorted(missing)}"


def test_env_example_has_no_orphan_vars() -> None:
    documented = _env_example_vars()
    expected = _settings_env_names() | _ENV_EXAMPLE_EXTRAS
    orphans = documented - expected
    assert not orphans, (
        f"Variables in .env.example with no Settings field "
        f"(add to _ENV_EXAMPLE_EXTRAS if intentional): {sorted(orphans)}"
    )


def test_registered_commands_are_documented_in_readme() -> None:
    registered = _registered_commands()
    documented = _readme_commands()
    missing = registered - documented
    assert not missing, f"Telegram commands not mentioned in README: {sorted(missing)}"


def test_documented_commands_are_registered_in_bot() -> None:
    documented = _readme_commands()
    registered = _registered_commands()
    missing = documented - registered
    assert not missing, f"Commands in README table not registered in bot.py: {sorted(missing)}"
