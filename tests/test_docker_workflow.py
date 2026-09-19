"""Deterministic checks on the PR image-build-and-scan workflow.

Mirrors tests/test_consistency.py's shape: raw-text reads, regex parsing, no
YAML dependency. Keeps the PR image gate from silently drifting from the
release gate it is meant to mirror without inheriting its push/login/sign
steps.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKER_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "docker.yml"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"

_PINNED_USES = re.compile(r"uses:\s+[\w./-]+@[0-9a-f]{40}\s+#\s+v")


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
    docker_text = DOCKER_WORKFLOW.read_text(encoding="utf-8")
    release_text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    for value in ("severity: CRITICAL,HIGH", 'exit-code: "1"', "ignore-unfixed: true"):
        assert value in docker_text, f"docker.yml's Trivy scan is missing {value!r}"
        assert value in release_text, f"release.yml's Trivy scan is missing {value!r}"


def test_import_check_runs_outside_the_copied_source() -> None:
    text = DOCKER_WORKFLOW.read_text(encoding="utf-8")
    match = re.search(r"run:\s*(docker run .+)$", text, re.MULTILINE)
    assert match is not None, "docker.yml is missing the import smoke check's run: line"
    run_line = match.group(1)
    runs_outside_app = "-w /" in run_line
    assert runs_outside_app, "import check needs -w / or it imports the copied source at /app"
    imports_cleanrr = "import cleanrr" in run_line
    assert imports_cleanrr, "import check must import cleanrr to prove the install worked"


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
