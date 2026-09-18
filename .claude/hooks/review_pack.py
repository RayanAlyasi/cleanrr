"""Write one file that gives a review agent the whole change up front.

    python .claude/hooks/review_pack.py [--base main] [--plan .claude/plans/x.md] [--gate gate.txt]

The pack holds the commit list, the changed files with line counts, the diff with 25
lines of context, the gate output, and the plan's "Review focus" section. It replaces
the dozen exploratory calls a reviewer otherwise spends finding the change; it does not
limit what the reviewer may read afterwards. Prints the path it wrote.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

MAX_DIFF_CHARS = 200_000


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
        errors="replace",
    ).stdout


def _review_focus(plan: Path) -> str:
    text = plan.read_text(encoding="utf-8")
    match = re.search(r"^## Review focus\s*\n(.*?)(?=^## |\Z)", text, flags=re.S | re.M)
    return match.group(1).strip() if match else "(the plan has no Review focus section)"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="main")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--gate", type=Path, help="file holding the gate output to include")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    branch = _git("rev-parse", "--abbrev-ref", "HEAD").strip()
    span = f"{args.base}...HEAD"
    diff = _git("diff", "-U25", span)
    truncated = len(diff) > MAX_DIFF_CHARS
    if truncated:
        diff = diff[:MAX_DIFF_CHARS]

    parts = [
        f"# Review pack: {branch} against {args.base}",
        "Read this first. It is a starting point, not a boundary: open any file you need.",
        "## Commits\n```\n" + _git("log", "--oneline", f"{args.base}..HEAD").strip() + "\n```",
        "## Changed files\n```\n" + _git("diff", "--stat", span).strip() + "\n```",
    ]
    if args.plan is not None:
        parts.append(f"## Review focus (from {args.plan.as_posix()})\n" + _review_focus(args.plan))
    if args.gate is not None:
        parts.append(
            "## Gate output\n```\n" + args.gate.read_text(encoding="utf-8").strip() + "\n```"
        )
    note = (
        f"\nThe diff was cut at {MAX_DIFF_CHARS:,} characters; see `git diff {span}`.\n"
        if truncated
        else ""
    )
    parts.append(f"## Diff (`git diff -U25 {span}`)\n{note}```diff\n{diff}\n```")

    out = args.out or Path(".claude/plans") / f"review-pack-{branch.replace('/', '-')}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n\n".join(parts) + "\n", encoding="utf-8", newline="\n")
    print(out.as_posix())
    return 0


if __name__ == "__main__":
    sys.exit(main())
