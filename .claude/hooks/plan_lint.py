"""Check the structure of a cleanrr plan before anyone spends turns reading it.

    python .claude/hooks/plan_lint.py .claude/plans/x.md    lint one plan
    python .claude/hooks/plan_lint.py --hook                planner Stop hook

Only structure is checked: the header lines, the sections, every task's headings, the
commit subjects, and that no two tasks in a wave own the same file. Whether the design
is right stays with the plan review. A plan whose Status is `needs-user-decision` is
checked for its header only, since it may stop before any task exists.

As a hook it lints the plan written during this run and exits 2 with the problems on
stderr, which sends the planner back once. A second stop is let through so a rule this
script gets wrong cannot hold the planner until its turn limit.
"""

import json
import os
import re
import sys
import threading
import time
from pathlib import Path

PLANS = Path(".claude/plans")
HOOK_WINDOW_SECONDS = 3 * 60 * 60
STATUSES = ("ready", "needs-user-decision")
SECTIONS = ("Goal", "Design", "Decisions", "Verified assumptions", "Unverified", "Review focus")
TASK_LABELS = (
    "Goal",
    "Files",
    "Out of scope",
    "Existing code to follow",
    "Steps",
    "Tests",
    "Verification",
    "Invariants",
    "Docs and compliance",
    "Commit subject",
)
SUBJECT_MAX = 50
TASK_FILES_MAX = 8
WIDE_TASK_MARK = "Wide by necessity:"
SUBJECT = re.compile(
    r"^(feat|fix|perf|deps|docs|chore|ci|refactor|test|build)(\([a-z0-9_-]+\))?!?: \S.*[^.\s]$"
)
LABEL = re.compile(r"^\*\*([A-Za-z ]+)\*\*")
PATH_TOKEN = re.compile(r"`([^`\s]+)`")


def _subject_problems(where: str, subject: str) -> list[str]:
    problems: list[str] = []
    if not SUBJECT.match(subject):
        problems.append(f"{where}: `{subject}` is not a Conventional Commit subject")
    if len(subject) > SUBJECT_MAX:
        problems.append(f"{where}: subject is {len(subject)} characters (limit {SUBJECT_MAX})")
    return problems


def _header(lines: list[str], key: str) -> str | None:
    for line in lines[:12]:
        if line.startswith(f"{key}:"):
            return line.split(":", 1)[1].strip()
    return None


def _task_blocks(lines: list[str]) -> list[tuple[str, str, list[str]]]:
    blocks: list[tuple[str, str, list[str]]] = []
    wave = ""
    for line in lines:
        if line.startswith("## "):
            wave = line[3:].strip() if line.startswith("## Wave") else ""
        elif line.startswith("### Task") and wave:
            blocks.append((wave, line[4:].strip(), []))
        elif blocks and wave and blocks[-1][0] == wave:
            blocks[-1][2].append(line)
    return blocks


def _label_bodies(body: list[str]) -> dict[str, list[str]]:
    bodies: dict[str, list[str]] = {}
    current = ""
    for line in body:
        match = LABEL.match(line)
        if match:
            current = match.group(1).strip()
            bodies[current] = [line[match.end() :]]
        elif current:
            bodies[current].append(line)
    return bodies


def _owned_files(files_body: list[str]) -> list[str]:
    owned: list[str] = []
    for line in files_body:
        if not line.lstrip().startswith(("-", "*")):
            continue
        token = PATH_TOKEN.search(line)
        if token and ("/" in token.group(1) or "." in token.group(1)):
            owned.append(token.group(1).replace("\\", "/").removeprefix("./"))
    return owned


def lint(text: str) -> list[str]:
    lines = text.splitlines()
    problems: list[str] = []

    status = _header(lines, "Status")
    if status not in STATUSES:
        problems.append(f"header: `Status:` must be one of {', '.join(STATUSES)}")
    if not _header(lines, "Branch"):
        problems.append("header: `Branch:` is missing")
    pr_title = _header(lines, "PR title")
    if not pr_title:
        problems.append("header: `PR title:` is missing")
    else:
        problems.extend(_subject_problems("PR title", pr_title))
    if status == "needs-user-decision":
        return problems

    headings = {line[3:].strip() for line in lines if line.startswith("## ")}
    problems.extend(f"section `## {name}` is missing" for name in SECTIONS if name not in headings)

    tasks = _task_blocks(lines)
    if not tasks:
        problems.append("no `### Task` under any `## Wave` heading")

    owners: dict[tuple[str, str], str] = {}
    for wave, title, body in tasks:
        name = title.split("—")[0].strip()
        bodies = _label_bodies(body)
        problems.extend(
            f"{name}: `**{label}**` is missing" for label in TASK_LABELS if label not in bodies
        )
        subject_line = " ".join(bodies.get("Commit subject", []))
        quoted = re.search(r"`([^`]+)`", subject_line)
        # A rationale may follow the subject; only the backticked part is the subject.
        subject = quoted.group(1) if quoted else subject_line.strip(" —-")
        if subject:
            problems.extend(_subject_problems(name, subject))
        files_body = bodies.get("Files", [])
        owned = _owned_files(files_body)
        if "Files" in bodies and not owned:
            problems.append(f"{name}: `**Files**` lists no path in backticks")
        if len(owned) > TASK_FILES_MAX and WIDE_TASK_MARK not in "\n".join(files_body):
            problems.append(
                f"{name}: owns {len(owned)} files (limit {TASK_FILES_MAX}); split it, or add a"
                f" `{WIDE_TASK_MARK} <reason>` line under **Files**"
            )
        for path in owned:
            other = owners.setdefault((wave, path), name)
            if other != name:
                problems.append(f"{wave}: `{path}` is owned by both {other} and {name}")
    return problems


def _hook_input() -> dict[str, object]:
    chunks: list[bytes] = []

    def drain() -> None:
        while chunk := sys.stdin.buffer.read1(65536):
            chunks.append(chunk)

    if not sys.stdin.isatty():
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        reader.join(3.0)
    try:
        value = json.loads(b"".join(chunks) or b"{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _plan_written_this_run() -> Path | None:
    cutoff = time.time() - HOOK_WINDOW_SECONDS
    plans = [
        p
        for p in PLANS.glob("*.md")
        if not p.name.startswith("review-pack-")
        and "-fixes-" not in p.name
        and p.stat().st_mtime >= cutoff
    ]
    return max(plans, key=lambda p: p.stat().st_mtime, default=None)


def main() -> int:
    args = sys.argv[1:]
    if args == ["--hook"]:
        if _hook_input().get("stop_hook_active"):
            return 0
        plan = _plan_written_this_run()
        if plan is None:
            return 0
    elif len(args) == 1:
        plan = Path(args[0])
    else:
        print(__doc__, file=sys.stderr)
        return 1

    problems = lint(plan.read_text(encoding="utf-8"))
    if not problems:
        print(f"plan lint: {plan.as_posix()} ok")
        return 0
    print(f"plan lint: {plan.as_posix()} has {len(problems)} problem(s):", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # A reader thread still blocked on an open stdin must not keep the hook alive.
    os._exit(code)
