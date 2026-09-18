"""Pick the /cleanrr-ship lane from the files a change touches.

    python .claude/hooks/lane.py --paths README.md cleanrr/x.py    before work, from the request
    python .claude/hooks/lane.py [--base main]                     after work, from the diff

Prints `full` or `light` on the first line and the reason on the second. Both lanes
run the reviewer and the security agent; the light lane only skips the planner. Anything
this script does not recognise is `full`.
"""

import argparse
import subprocess
import sys

LIGHT_MAX_SOURCE_LINES = 50

FULL_LANE_PREFIXES = (
    "cleanrr/permissions/",
    "cleanrr/tools/",
    ".github/",
    ".claude/",
)
FULL_LANE_FILES = frozenset(
    {
        "cleanrr/identity.py",
        "cleanrr/agent.py",
        "cleanrr/agent_pool.py",
        "cleanrr/config.py",
        "cleanrr/handlers.py",
        "cleanrr/bot.py",
        "Dockerfile",
        "docker-compose.yml",
        "pyproject.toml",
        ".pre-commit-config.yaml",
        ".gitattributes",
        ".gitignore",
    }
)
DOC_SUFFIXES = (".md",)
DOC_FILES = frozenset({".env.example", "LICENSE"})
DOC_PREFIXES = ("assets/",)


def _norm(path: str) -> str:
    return path.strip().replace("\\", "/").removeprefix("./")


def _is_doc(path: str) -> bool:
    return path.endswith(DOC_SUFFIXES) or path in DOC_FILES or path.startswith(DOC_PREFIXES)


def decide(paths: list[str], source_lines: int | None) -> tuple[str, str]:
    paths = [p for p in (_norm(p) for p in paths) if p]
    if not paths:
        return "full", "no paths given"
    for p in paths:
        if p in FULL_LANE_FILES or p.startswith(FULL_LANE_PREFIXES):
            return "full", f"{p} is on the full-lane list"
    code = [p for p in paths if not _is_doc(p)]
    if not code:
        return "light", "docs only"
    unknown = [p for p in code if not p.startswith(("cleanrr/", "tests/"))]
    if unknown:
        return "full", f"{unknown[0]} is outside cleanrr/, tests/ and docs"
    source = [p for p in code if p.startswith("cleanrr/")]
    if len(source) > 1:
        return "full", f"{len(source)} source files"
    if source_lines is not None and source_lines > LIGHT_MAX_SOURCE_LINES:
        return "full", f"{source_lines} changed source lines (limit {LIGHT_MAX_SOURCE_LINES})"
    return "light", "one small source file at most, outside the full-lane list"


def _diff(base: str) -> tuple[list[str], int]:
    out = subprocess.run(
        ["git", "diff", "--numstat", f"{base}...HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    paths: list[str] = []
    source_lines = 0
    for line in out.splitlines():
        added, deleted, path = line.split("\t", 2)
        path = _norm(path)
        paths.append(path)
        if path.startswith("cleanrr/"):
            # Binary files report "-"; count them as over the limit.
            if added == "-" or deleted == "-":
                source_lines += LIGHT_MAX_SOURCE_LINES + 1
            else:
                source_lines += int(added) + int(deleted)
    return paths, source_lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--paths", nargs="*")
    parser.add_argument("--base", default="main")
    args = parser.parse_args()
    if args.paths is not None:
        lane, reason = decide(args.paths, None)
    else:
        try:
            paths, source_lines = _diff(args.base)
        except (subprocess.CalledProcessError, ValueError, OSError) as exc:
            lane, reason = "full", f"could not read the diff ({exc.__class__.__name__})"
        else:
            lane, reason = decide(paths, source_lines)
    print(lane)
    print(reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
