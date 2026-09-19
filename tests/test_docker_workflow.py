"""Deterministic checks on the PR image-build-and-scan workflow.

Mirrors tests/test_consistency.py's shape: raw-text reads, regex parsing, no
YAML dependency. Keeps the PR image gate from silently drifting from the
release gate it is meant to mirror without inheriting its push/login/sign
steps.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKER_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "docker.yml"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"

_PINNED_USES = re.compile(r"uses:\s+[\w./-]+@[0-9a-f]{40}\s+#\s+v")


def _docker_run_workdir(run_line: str) -> str | None:
    tokens = run_line.split()
    for index, token in enumerate(tokens):
        if token in ("-w", "--workdir") and index + 1 < len(tokens):
            return tokens[index + 1]
    return None


def _trivy_steps(text: str) -> list[tuple[str, dict[str, str]]]:
    lines = text.splitlines()
    steps: list[tuple[str, dict[str, str]]] = []
    for index, line in enumerate(lines):
        if "uses: aquasecurity/trivy-action@" not in line:
            continue
        action_ref = line.split("uses:", 1)[1].split(" #", 1)[0].strip()
        with_index: int | None = None
        for offset in range(index + 1, len(lines)):
            if lines[offset].strip() == "with:":
                with_index = offset
                break
        assert with_index is not None, f"Trivy step at line {index + 1} has no with: block"
        with_indent = len(lines[with_index]) - len(lines[with_index].lstrip())
        mapping: dict[str, str] = {}
        for candidate in lines[with_index + 1 :]:
            if candidate.strip() == "":
                continue
            candidate_indent = len(candidate) - len(candidate.lstrip())
            if candidate_indent <= with_indent:
                break
            stripped = candidate.strip()
            if stripped.startswith("#"):
                continue
            key, _, value = stripped.partition(":")
            mapping[key.strip()] = value.strip()
        steps.append((action_ref, mapping))
    return steps


def test_workflow_runs_on_pull_requests_and_main() -> None:
    text = DOCKER_WORKFLOW.read_text(encoding="utf-8")
    runs_on_pull_request = "pull_request:" in text
    assert runs_on_pull_request, "docker.yml must trigger on pull_request"
    push_main_pattern = r"push:\s*\n\s*branches:\s*\[main\]"
    runs_on_main_push = re.search(push_main_pattern, text) is not None
    assert runs_on_main_push, "docker.yml needs push: branches: [main] to warm the PR cache"


def test_workflow_keeps_least_privilege_permissions() -> None:
    text = DOCKER_WORKFLOW.read_text(encoding="utf-8")
    permissions_pattern = r"(?m)^permissions:\n\s+contents:\s*read\s*$"
    has_read_only_permissions = re.search(permissions_pattern, text) is not None
    assert has_read_only_permissions, "docker.yml must grant only contents: read"
    has_no_write_permission = "write" not in text
    assert has_no_write_permission, "docker.yml must grant no write permission"


def test_every_action_is_pinned_to_a_commit_sha() -> None:
    text = DOCKER_WORKFLOW.read_text(encoding="utf-8")
    uses_count = len(re.findall(r"uses:", text))
    pinned_count = len(_PINNED_USES.findall(text))
    message = f"every uses: line must pin a SHA ({uses_count} uses:, {pinned_count} pinned)"
    assert uses_count == pinned_count, message


def test_image_is_not_pushed_or_signed() -> None:
    text = DOCKER_WORKFLOW.read_text(encoding="utf-8")
    assert "push: false" in text, "docker.yml's build step must set push: false"
    assert "push: true" not in text, "docker.yml must never push the image it builds"
    assert "docker/login-action" not in text, "docker.yml must never log in to a registry"
    assert "cosign" not in text, "docker.yml must never sign the image it builds"
    assert "ghcr.io" not in text, "docker.yml must not reference the release registry"


def test_build_is_amd64_only_and_loaded_locally() -> None:
    text = DOCKER_WORKFLOW.read_text(encoding="utf-8")
    assert "platforms: linux/amd64" in text, "docker.yml's build must target linux/amd64"
    assert "linux/arm64" not in text, "docker.yml's build must stay amd64-only"
    loads_locally = "load: true" in text
    assert loads_locally, "docker.yml's build needs load: true for Trivy and docker run"


def test_trivy_gate_matches_the_release_scan() -> None:
    docker_steps = _trivy_steps(DOCKER_WORKFLOW.read_text(encoding="utf-8"))
    release_steps = _trivy_steps(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    assert len(docker_steps) == 1, "docker.yml must have exactly one Trivy scan step"
    assert len(release_steps) == 1, "release.yml must have exactly one Trivy scan step"
    docker_action, docker_with = docker_steps[0]
    release_action, release_with = release_steps[0]
    assert docker_action == release_action, (
        "docker.yml's Trivy action must match release.yml's pinned SHA and version"
    )
    docker_gate = {key: value for key, value in docker_with.items() if key != "image-ref"}
    release_gate = {key: value for key, value in release_with.items() if key != "image-ref"}
    assert docker_gate == release_gate, (
        "docker.yml's Trivy gate has drifted from release.yml's; align docker.yml to match"
    )
    assert docker_gate["severity"] == "CRITICAL,HIGH", (
        "docker.yml's Trivy gate must scan for severity: CRITICAL,HIGH"
    )
    assert docker_gate["exit-code"] == '"1"', 'docker.yml\'s Trivy gate must set exit-code: "1"'
    assert docker_gate["ignore-unfixed"] == "true", (
        "docker.yml's Trivy gate must set ignore-unfixed: true"
    )


def test_trivy_step_parser_sees_a_diverging_gate() -> None:
    text = """      - name: Scan image for vulnerabilities
        uses: aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25 # v0.36.0
        with:
          image-ref: cleanrr:pr
          # widened for a one-off audit
          severity: CRITICAL,HIGH,MEDIUM
          exit-code: "1"
          ignore-unfixed: true
"""
    steps = _trivy_steps(text)
    assert len(steps) == 1, "the inline fixture must yield exactly one Trivy step"
    _, mapping = steps[0]
    assert mapping["severity"] == "CRITICAL,HIGH,MEDIUM"
    assert set(mapping) == {"image-ref", "severity", "exit-code", "ignore-unfixed"}, (
        "a comment line inside the with: block must not become a mapping key"
    )
    docker_mapping = _trivy_steps(DOCKER_WORKFLOW.read_text(encoding="utf-8"))[0][1]
    assert mapping != docker_mapping, "the parser must catch this gate diverging from docker.yml"


def test_import_check_runs_outside_the_copied_source() -> None:
    text = DOCKER_WORKFLOW.read_text(encoding="utf-8")
    match = re.search(r"run:\s*(docker run .+)$", text, re.MULTILINE)
    assert match is not None, "docker.yml is missing the import smoke check's run: line"
    run_line = match.group(1)
    runs_outside_app = _docker_run_workdir(run_line) == "/"
    assert runs_outside_app, "import check needs -w / or it imports the copied source at /app"
    imports_cleanrr = "import cleanrr" in run_line
    assert imports_cleanrr, "import check must import cleanrr to prove the install worked"


@pytest.mark.parametrize(
    ("run_line", "expected"),
    [
        ('docker run --rm -w /app cleanrr:pr python -c "import cleanrr"', "/app"),
        ('docker run --rm cleanrr:pr python -c "import cleanrr"', None),
        ('docker run --rm --workdir / cleanrr:pr python -c "import cleanrr"', "/"),
    ],
)
def test_docker_run_workdir_rejects_anything_but_root(run_line: str, expected: str | None) -> None:
    assert _docker_run_workdir(run_line) == expected


def test_cache_export_failure_does_not_fail_the_build() -> None:
    text = DOCKER_WORKFLOW.read_text(encoding="utf-8")
    assert "cache-to: type=gha,mode=max,ignore-error=true" in text, (
        "docker.yml's cache-to: must set ignore-error=true so a cache export "
        "failure doesn't fail the Build step"
    )


def test_no_workflow_expression_is_interpolated_into_a_run_script() -> None:
    text = DOCKER_WORKFLOW.read_text(encoding="utf-8")
    lines = text.splitlines()
    run_line_indices: set[int] = set()
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)run:\s*(.*)$", line)
        if match is None:
            continue
        indent, inline = match.groups()
        run_line_indices.add(index)
        if inline.strip() in {"", "|", ">", "|-", ">-", "|+", ">+"}:
            block_indent = len(indent)
            for offset in range(index + 1, len(lines)):
                candidate = lines[offset]
                if candidate.strip() == "":
                    run_line_indices.add(offset)
                    continue
                if len(candidate) - len(candidate.lstrip()) <= block_indent:
                    break
                run_line_indices.add(offset)
    offending = [lines[i] for i in sorted(run_line_indices) if "${{" in lines[i]]
    message = f"run: scripts must not interpolate workflow expressions: {offending}"
    assert not offending, message
