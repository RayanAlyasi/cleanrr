"""Deterministic drift checks between docs and code.

These run in CI and as part of /cleanrr-ship before invoking the reviewer agent —
they catch the cheap, mechanical mismatches so the LLM only spends tokens on the
judgement calls.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from cleanrr.config import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = REPO_ROOT / ".env.example"
README = REPO_ROOT / "README.md"
BOT_PY = REPO_ROOT / "cleanrr" / "bot.py"
CONSTRAINTS = REPO_ROOT / "constraints.txt"
PYPROJECT = REPO_ROOT / "pyproject.toml"
DOCKERFILE = REPO_ROOT / "Dockerfile"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PRE_COMMIT = REPO_ROOT / ".pre-commit-config.yaml"

# Settings fields that intentionally have no .env.example entry because they are
# meant for Docker compose interpolation or local-dev escape hatches, not the
# bot's pydantic-settings layer.
_ENV_EXAMPLE_EXTRAS = {"DOCKER_NETWORK_NAME"}

# Pinned for the semgrep CI job only. semgrep is a heavy scanner no
# contributor runs locally and gate.sh doesn't invoke, so it stays out of
# the dev extra while still being version-pinned.
_CI_ONLY_PINS = frozenset({"semgrep"})


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


def _declared_requirements() -> dict[str, Requirement]:
    data: dict[str, Any] = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = data["project"]
    lines = [*project["dependencies"], *project["optional-dependencies"]["dev"]]
    requirements = [Requirement(line) for line in lines]
    return {canonicalize_name(req.name): req for req in requirements}


def _constraint_pins(text: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        req = Requirement(line)
        clauses = list(req.specifier)
        assert len(clauses) == 1 and clauses[0].operator == "==", (
            f"constraints.txt line {line!r} must be a single exact `==` pin, e.g. 'package==1.2.3'"
        )
        assert not req.extras, (
            f"constraints.txt line {line!r} must not declare an extra "
            "(pip rejects extras in a constraints file)"
        )
        assert req.marker is None, (
            f"constraints.txt line {line!r} must not carry an environment marker"
        )
        pins[canonicalize_name(req.name)] = clauses[0].version
    return pins


def _constraints_text() -> str:
    return CONSTRAINTS.read_text(encoding="utf-8")


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


def test_every_declared_dependency_is_pinned() -> None:
    declared = _declared_requirements()
    pinned = _constraint_pins(_constraints_text())
    missing = set(declared) - set(pinned)
    assert not missing, f"Dependencies missing a pin in constraints.txt: {sorted(missing)}"


def test_constraints_have_no_orphan_pins() -> None:
    declared = _declared_requirements()
    pinned = _constraint_pins(_constraints_text())
    orphans = set(pinned) - set(declared) - _CI_ONLY_PINS
    assert not orphans, (
        "Pins in constraints.txt with no pyproject.toml dependency (declare it "
        "in pyproject.toml, add it to _CI_ONLY_PINS if deliberately CI-only, or "
        f"drop the pin): {sorted(orphans)}"
    )


def test_pins_satisfy_declared_ranges() -> None:
    declared = _declared_requirements()
    pinned = _constraint_pins(_constraints_text())
    for name in sorted(set(pinned) & set(declared)):
        specifier = declared[name].specifier
        assert specifier.contains(pinned[name]), (
            f"{name} is pinned to {pinned[name]} in constraints.txt, which falls "
            f"outside pyproject.toml's declared range {specifier}"
        )


@pytest.mark.parametrize(
    "line",
    [
        "ruff>=0.16.8",
        "ruff==0.16.8,!=0.16.7",
        "python-telegram-bot[ext]==22.8",
        'ruff==0.16.8; python_version < "3.13"',
    ],
)
def test_constraint_line_must_be_a_bare_exact_pin(line: str) -> None:
    with pytest.raises(AssertionError):
        _constraint_pins(line)


def test_precommit_ruff_rev_matches_pinned_ruff() -> None:
    match = re.search(r"ruff-pre-commit\s+rev:\s*v(\S+)", PRE_COMMIT.read_text(encoding="utf-8"))
    assert match, "Could not find the astral-sh/ruff-pre-commit rev in .pre-commit-config.yaml"
    pinned_ruff = _constraint_pins(_constraints_text())["ruff"]
    assert match.group(1) == pinned_ruff, (
        "The ruff-pre-commit rev and the ruff pin in constraints.txt must move "
        f"together in one commit: rev is v{match.group(1)}, pin is {pinned_ruff}"
    )


def test_dockerfile_installs_with_the_constraints_file() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "pip install -c constraints.txt ." in text, (
        "Dockerfile must install with `pip install -c constraints.txt .`"
    )
    match = re.search(r"^COPY pyproject\.toml.*$", text, flags=re.MULTILINE)
    assert match, "Dockerfile must copy pyproject.toml in its metadata COPY line"
    assert "constraints.txt" in match.group(), (
        "Dockerfile's metadata COPY line must include constraints.txt"
    )


def test_ci_installs_with_the_constraints_file() -> None:
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    assert 'pip install -c constraints.txt -e ".[dev]"' in text, (
        'ci.yml\'s check job must install with `pip install -c constraints.txt -e ".[dev]"`'
    )


def test_semgrep_job_installs_with_the_constraints_file() -> None:
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "pip install -c constraints.txt semgrep" in text, (
        "ci.yml's semgrep job must install with `pip install -c constraints.txt semgrep`"
    )
    assert "pip install semgrep" not in text, (
        "ci.yml must not fall back to a bare `pip install semgrep`"
    )


def test_ci_only_pins_are_installed_by_a_ci_job() -> None:
    workflow_text = CI_WORKFLOW.read_text(encoding="utf-8")
    declared = _declared_requirements()
    for name in sorted(_CI_ONLY_PINS):
        assert f"pip install -c constraints.txt {name}" in workflow_text, (
            f"{name} is in _CI_ONLY_PINS but no ci.yml job installs it with "
            f"`pip install -c constraints.txt {name}`"
        )
        assert name not in declared, (
            f"{name} is in _CI_ONLY_PINS but is also declared in pyproject.toml "
            "— it isn't CI-only, drop it from _CI_ONLY_PINS"
        )
