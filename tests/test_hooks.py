from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPO_ROOT / ".claude" / "hooks"


def _find_bash() -> str | None:
    if sys.platform == "win32":
        git_bash = Path(r"C:\Program Files\Git\usr\bin\bash.exe")
        if git_bash.exists():
            return str(git_bash)
    found = shutil.which("bash")
    # System32\bash.exe is the WSL launcher; it can't see Windows paths.
    if found is not None and "system32" in found.lower():
        return None
    return found


BASH = _find_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="hook scripts need bash")


def _run(script: str, *args: str, stdin: str) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    return subprocess.run(
        [BASH, f".claude/hooks/{script}", *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO_ROOT,
        check=False,
    )


def _write_call(file_path: str, content: str = "x") -> str:
    return json.dumps(
        {"tool_name": "Write", "tool_input": {"file_path": file_path, "content": content}}
    )


def _agent_call(subagent_type: str) -> str:
    return json.dumps(
        {"tool_name": "Agent", "tool_input": {"subagent_type": subagent_type, "prompt": "p"}}
    )


@pytest.mark.parametrize(
    ("mode", "file_path", "expected"),
    [
        ("coder", "cleanrr/agent.py", 0),
        ("coder", "CHANGELOG.md", 2),
        ("coder", r"C:\repo\cleanrr\CHANGELOG.md", 2),
        ("coder", ".release-please-manifest.json", 2),
        ("coder", "/home/dev/cleanrr/.release-please-config.json", 2),
        ("planner", ".claude/plans/queue-diagnostics.md", 0),
        ("planner", r"C:\repo\cleanrr\.claude\plans\x.md", 0),
        ("planner", "cleanrr/bot.py", 2),
        ("planner", "README.md", 2),
        ("reviewer", ".claude/agent-memory/cleanrr-reviewer/MEMORY.md", 0),
        ("reviewer", r"C:\repo\cleanrr\.claude\agent-memory-local\cleanrr-security\n.md", 0),
        ("reviewer", "cleanrr/agent.py", 2),
    ],
)
def test_protect_paths_by_role(mode: str, file_path: str, expected: int) -> None:
    result = _run("protect-paths.sh", mode, stdin=_write_call(file_path))
    assert result.returncode == expected, result.stderr
    assert ("blocked:" in result.stderr) == (expected == 2)


def test_protect_paths_handles_a_large_write_payload() -> None:
    """bash `read` drains a pipe byte by byte and timed out on payloads this size,
    which blocked legitimate writes; the helper reads in chunks."""
    payload = _write_call("CHANGELOG.md", content="line / with 'quotes'\n" * 50_000)
    assert len(payload) > 1_000_000
    assert _run("protect-paths.sh", "coder", stdin=payload).returncode == 2
    allowed = _write_call("cleanrr/agent.py", content="y" * 1_000_000)
    assert _run("protect-paths.sh", "coder", stdin=allowed).returncode == 0


@pytest.mark.parametrize("script_args", [("protect-paths.sh", "coder"), ("gate.sh", "pytest")])
def test_hooks_fail_closed_on_unparseable_input(script_args: tuple[str, str]) -> None:
    result = _run(*script_args, "--version", stdin="not json")
    assert result.returncode == 2
    assert "could not parse" in result.stderr


def test_protect_paths_allows_a_call_without_a_file_path() -> None:
    assert _run("protect-paths.sh", "coder", stdin=json.dumps({"tool_input": {}})).returncode == 0


@pytest.mark.parametrize(
    ("requested", "expected"),
    [("cleanrr-architect", 0), ("cleanrr-coder", 2), ("general-purpose", 2), ("", 2)],
)
def test_allow_subagent_permits_only_listed_types(requested: str, expected: int) -> None:
    result = _run("allow-subagent.sh", "cleanrr-architect", stdin=_agent_call(requested))
    assert result.returncode == expected, result.stderr


def test_allow_subagent_fails_closed_on_unparseable_input() -> None:
    assert _run("allow-subagent.sh", "cleanrr-architect", stdin="junk").returncode == 2


def test_gate_runs_in_the_hook_supplied_cwd() -> None:
    hook_input = json.dumps({"cwd": str(REPO_ROOT), "hook_event_name": "SubagentStop"})
    result = _run("gate.sh", "pytest", "--version", stdin=hook_input)
    assert result.returncode == 0, result.stderr
    assert "pytest" in result.stdout + result.stderr


def test_gate_refuses_a_cwd_that_does_not_exist() -> None:
    hook_input = json.dumps({"cwd": str(REPO_ROOT / "no-such-worktree")})
    result = _run("gate.sh", "pytest", "--version", stdin=hook_input)
    assert result.returncode == 2
    assert "cannot cd" in result.stderr


def test_gate_falls_back_to_the_current_directory_without_hook_input() -> None:
    assert _run("gate.sh", "pytest", "--version", stdin="").returncode == 0


def test_hook_field_extracts_nested_values_and_tolerates_missing_ones() -> None:
    def field(path: str, stdin: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(HOOKS / "hook_field.py"), path],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    assert field("tool_input.file_path", _write_call("a/b.py")).stdout == "a/b.py"
    assert field("tool_input.missing", _write_call("a/b.py")).stdout == ""
    assert field("cwd.nested", json.dumps({"cwd": "/x"})).stdout == ""
    assert field("cwd", "").returncode == 0
    assert field("cwd", "{broken").returncode == 3
