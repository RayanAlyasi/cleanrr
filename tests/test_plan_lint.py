from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".claude" / "hooks" / "plan_lint.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("plan_lint", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


plan_lint = _load()


def _task(number: str, files: list[str], subject: str = "fix: tighten the thing") -> str:
    listed = "\n".join(f"- `{path}` — edit" for path in files)
    return f"""### Task {number} — Do the thing

**Goal** — The thing is done.

**Files**
{listed}

**Out of scope** — Everything else.

**Existing code to follow** — `cleanrr/metrics.py:10-20`.

**Steps**
1. Do it.

**Tests**
- One test.

**Verification** — `bash .claude/hooks/gate.sh`. All five checks green.

**Invariants**
- None broken.

**Docs and compliance** — none.

**Commit subject** — `{subject}`
"""


def _plan(waves: dict[str, list[str]], status: str = "ready") -> str:
    body = "\n".join(f"## {name}\n\n" + "\n".join(tasks) for name, tasks in waves.items())
    return f"""# Plan: A change
Status: {status}
Branch: fix/a-change
PR title: fix: tighten the thing

## Goal
One sentence.

## Design
Two sentences.

## Decisions
None.

## Verified assumptions
- None needed.

## Unverified
- Nothing.

{body}

## Review focus
- The thing.
"""


def _valid() -> str:
    return _plan(
        {
            "Wave 1 (parallel)": [
                _task("1.1", ["cleanrr/metrics.py", "tests/test_metrics.py"]),
                _task("1.2", ["cleanrr/bot.py", "tests/test_bot.py"]),
            ],
            "Wave 2 (after wave 1)": [_task("2.1", ["README.md", "cleanrr/metrics.py"])],
        }
    )


def test_a_complete_plan_has_no_problems() -> None:
    assert plan_lint.lint(_valid()) == []


def test_a_file_may_be_owned_again_in_a_later_wave_but_not_twice_in_one() -> None:
    clash = _plan(
        {
            "Wave 1 (parallel)": [
                _task("1.1", ["cleanrr/metrics.py"]),
                _task("1.2", ["./cleanrr/metrics.py", "tests/test_bot.py"]),
            ]
        }
    )
    problems = plan_lint.lint(clash)
    assert problems == [
        "Wave 1 (parallel): `cleanrr/metrics.py` is owned by both Task 1.1 and Task 1.2"
    ]


@pytest.mark.parametrize("label", ["Invariants", "Commit subject", "Out of scope", "Files"])
def test_a_missing_task_heading_is_reported_by_task(label: str) -> None:
    text = _valid().replace(f"**{label}**", f"**{label} (tbd)**", 1)
    assert f"Task 1.1: `**{label}**` is missing" in plan_lint.lint(text)


@pytest.mark.parametrize(
    ("subject", "fragment"),
    [
        ("fix: " + "x" * 46, "subject is 51 characters"),
        ("Fixed the thing", "is not a Conventional Commit subject"),
        ("fix: tighten the thing.", "is not a Conventional Commit subject"),
        ("feature: add a thing", "is not a Conventional Commit subject"),
    ],
)
def test_a_bad_commit_subject_is_reported(subject: str, fragment: str) -> None:
    text = _plan({"Wave 1": [_task("1.1", ["cleanrr/metrics.py"], subject)]})
    problems = plan_lint.lint(text)
    assert len(problems) == 1 and fragment in problems[0] and problems[0].startswith("Task 1.1")


def test_a_rationale_after_the_backticked_subject_is_allowed() -> None:
    text = _valid().replace(
        "`fix: tighten the thing`\n", "`fix: tighten the thing`  A user-visible bug.\n", 1
    )
    assert plan_lint.lint(text) == []


def test_the_pr_title_is_held_to_the_same_rule() -> None:
    text = _valid().replace("PR title: fix: tighten the thing", "PR title: Tighten things")
    assert plan_lint.lint(text) == [
        "PR title: `Tighten things` is not a Conventional Commit subject"
    ]


def test_a_wide_task_needs_a_stated_reason() -> None:
    files = [f"cleanrr/module_{i}.py" for i in range(plan_lint.TASK_FILES_MAX + 1)]
    wide = _plan({"Wave 1": [_task("1.1", files)]})
    assert any("owns 9 files" in problem for problem in plan_lint.lint(wide))
    excused = wide.replace("**Files**\n", "**Files**\nWide by necessity: one rename.\n", 1)
    assert plan_lint.lint(excused) == []


def test_a_missing_section_and_a_plan_without_tasks_are_reported() -> None:
    text = _valid().replace("## Unverified\n", "## Open questions\n")
    assert plan_lint.lint(text) == ["section `## Unverified` is missing"]
    assert "no `### Task` under any `## Wave` heading" in plan_lint.lint(_plan({}))


def test_a_plan_waiting_on_the_owner_is_checked_for_its_header_only() -> None:
    assert plan_lint.lint(_plan({}, status="needs-user-decision")) == []
    assert plan_lint.lint(_plan({}, status="draft")) != []


def _run_hook(cwd: Path, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--hook"],
        cwd=cwd,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_the_hook_blocks_once_on_a_bad_plan_and_never_without_one(tmp_path: Path) -> None:
    assert _run_hook(tmp_path, {}).returncode == 0

    plans = tmp_path / ".claude" / "plans"
    plans.mkdir(parents=True)
    (plans / "review-pack-x.md").write_text("not a plan", encoding="utf-8")
    assert _run_hook(tmp_path, {}).returncode == 0

    (plans / "a-change.md").write_text(_valid().replace("**Tests**", "**Test**"), encoding="utf-8")
    blocked = _run_hook(tmp_path, {"stop_hook_active": False})
    assert blocked.returncode == 2
    assert "`**Tests**` is missing" in blocked.stderr
    assert _run_hook(tmp_path, {"stop_hook_active": True}).returncode == 0

    (plans / "a-change.md").write_text(_valid(), encoding="utf-8")
    assert _run_hook(tmp_path, {}).returncode == 0
