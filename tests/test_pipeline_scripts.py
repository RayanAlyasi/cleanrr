from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPO_ROOT / ".claude" / "hooks"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, HOOKS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lane = _load("lane")
usage_report = _load("usage_report")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "cleanrr").mkdir()
    (tmp_path / "cleanrr" / "metrics.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# readme\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    return tmp_path


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["README.md", "THREAT_MODEL.md", ".env.example"], "light"),
        (["cleanrr/metrics.py", "tests/test_metrics.py"], "light"),
        (["tests/test_metrics.py"], "light"),
        ([r"cleanrr\metrics.py"], "light"),
        (["cleanrr/agent.py"], "full"),
        (["cleanrr/handlers.py", "README.md"], "full"),
        (["cleanrr/tools/_results.py"], "full"),
        (["cleanrr/permissions/_registry.py"], "full"),
        (["cleanrr/metrics.py", "cleanrr/__init__.py"], "full"),
        ([".github/workflows/ci.yml"], "full"),
        (["Dockerfile"], "full"),
        (["pyproject.toml"], "full"),
        ([".claude/agents/cleanrr-coder.md"], "full"),
        (["scripts/anything.sh"], "full"),
        (["cleanrr_new/thing.py"], "full"),
        ([], "full"),
    ],
)
def test_lane_from_requested_paths(paths: list[str], expected: str) -> None:
    assert lane.decide(paths, None)[0] == expected


def test_lane_counts_only_source_lines_against_the_limit() -> None:
    small = ["cleanrr/metrics.py", "tests/test_metrics.py"]
    assert lane.decide(small, lane.LIGHT_MAX_SOURCE_LINES)[0] == "light"
    assert lane.decide(small, lane.LIGHT_MAX_SOURCE_LINES + 1)[0] == "full"


def test_lane_from_a_real_diff(repo: Path) -> None:
    script = str(HOOKS / "lane.py")
    (repo / "cleanrr" / "metrics.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "small")
    out = subprocess.run(
        [sys.executable, script], cwd=repo, capture_output=True, text=True, check=True
    )
    assert out.stdout.splitlines()[0] == "light"

    (repo / "cleanrr" / "metrics.py").write_text(
        "\n".join(f"v{i} = {i}" for i in range(80)), encoding="utf-8"
    )
    _git(repo, "commit", "-q", "-am", "large")
    out = subprocess.run(
        [sys.executable, script], cwd=repo, capture_output=True, text=True, check=True
    )
    assert out.stdout.splitlines()[0] == "full"


def test_lane_is_full_when_the_diff_cannot_be_read(tmp_path: Path) -> None:
    out = subprocess.run(
        [sys.executable, str(HOOKS / "lane.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.splitlines()[0] == "full"


def test_review_pack_holds_the_diff_the_focus_and_the_gate_output(repo: Path) -> None:
    (repo / "cleanrr" / "metrics.py").write_text("x = 42\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "fix: answer differently")
    plan = repo / "plan.md"
    plan.write_text(
        "# Plan\n\n## Review focus\n- watch the answer\n\n## Other\nignored\n", encoding="utf-8"
    )
    gate = repo / "gate.txt"
    gate.write_text("ok    pytest\n", encoding="utf-8")
    out = subprocess.run(
        [
            sys.executable,
            str(HOOKS / "review_pack.py"),
            "--plan",
            "plan.md",
            "--gate",
            "gate.txt",
            "--out",
            "pack.md",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "pack.md"
    pack = (repo / "pack.md").read_text(encoding="utf-8")
    assert "fix: answer differently" in pack
    assert "+x = 42" in pack
    assert "- watch the answer" in pack
    assert "ignored" not in pack
    assert "ok    pytest" in pack


def _assistant(message_id: str, model: str, **usage: int | dict[str, int]) -> str:
    return json.dumps(
        {"type": "assistant", "message": {"id": message_id, "model": model, "usage": usage}}
    )


def test_usage_summary_dedupes_streamed_blocks_and_prices_by_model(tmp_path: Path) -> None:
    transcript = tmp_path / "agent.jsonl"
    transcript.write_text(
        "\n".join(
            [
                _assistant(
                    "m1", "claude-opus-5", cache_read_input_tokens=1_000_000, output_tokens=10
                ),
                _assistant(
                    "m1", "claude-opus-5", cache_read_input_tokens=1_000_000, output_tokens=400
                ),
                _assistant(
                    "m2", "claude-opus-5", cache_creation_input_tokens=100_000, input_tokens=5
                ),
                json.dumps({"type": "user", "message": {"content": "ignored"}}),
                "not json",
            ]
        ),
        encoding="utf-8",
    )
    summary = usage_report.summarise(transcript)
    assert summary["turns"] == 2
    assert summary["output_tokens"] == 400
    assert summary["cache_read_input_tokens"] == 1_000_000
    assert summary["context_end"] == 100_005
    # Per MTok: cache reads $0.50, 1h cache writes $10, fresh input $5, output $25.
    assert summary["cost"] == pytest.approx(0.5 + 1.0 + 0.000025 + 0.01)


def test_usage_summary_prices_five_minute_cache_writes_below_one_hour_writes(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "subagent.jsonl"
    transcript.write_text(
        "\n".join(
            [
                _assistant(
                    "m1",
                    "claude-opus-5",
                    cache_creation_input_tokens=1_000_000,
                    cache_creation={"ephemeral_5m_input_tokens": 1_000_000},
                ),
                _assistant(
                    "m2",
                    "claude-opus-5",
                    cache_creation_input_tokens=1_000_000,
                    cache_creation={"ephemeral_1h_input_tokens": 1_000_000},
                ),
            ]
        ),
        encoding="utf-8",
    )
    # Per MTok on a $5 base: 5-minute writes $6.25, 1-hour writes $10.
    assert usage_report.summarise(transcript)["cost"] == pytest.approx(6.25 + 10.0)


def test_usage_summary_prices_fable_cache_reads_lower_and_leaves_unknown_models_unpriced(
    tmp_path: Path,
) -> None:
    fable = tmp_path / "fable.jsonl"
    fable.write_text(
        _assistant("m1", "claude-fable-5-1", cache_read_input_tokens=1_000_000), encoding="utf-8"
    )
    assert usage_report.summarise(fable)["cost"] == pytest.approx(0.25)
    other = tmp_path / "other.jsonl"
    other.write_text(_assistant("m1", "some-future-model", output_tokens=5), encoding="utf-8")
    assert usage_report.summarise(other)["cost"] is None
