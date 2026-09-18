#!/usr/bin/env bash
# Quality gate: the same checks CI runs, in one command.
#
#   bash .claude/hooks/gate.sh                 full gate; also the coder's Stop hook
#   bash .claude/hooks/gate.sh pytest -k foo   run one tool through the project interpreter
#
# Exits 2 on failure so Claude Code's Stop / SubagentStop hooks block the agent
# from finishing red. Prints failures to stderr, which is what the agent sees.
set -u

# Hook invocations pass JSON on stdin whose "cwd" is the active worktree; the
# hook process itself may start in the main checkout. A manual run has no stdin.
here=$(cd "$(dirname "$0")" && pwd)
python_bin=$(command -v python || command -v python3)
dir=$("$python_bin" "$here/hook_field.py" cwd)
if [ $? -eq 3 ]; then
  echo "gate: could not parse hook input; refusing to guess which checkout to test." >&2
  exit 2
fi
if [ -n "$dir" ]; then
  command -v cygpath >/dev/null 2>&1 && dir=$(cygpath -u "$dir")
  cd "$dir" || { echo "gate: cannot cd to $dir" >&2; exit 2; }
fi

# Worktrees share .git but not .venv, so resolve the interpreter from the main
# checkout. Fall back to whatever python is on PATH.
main=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
main=${main%/.git}
py=$python_bin
for cand in "$main/.venv/Scripts/python.exe" "$main/.venv/bin/python"; do
  if [ -x "$cand" ]; then py=$cand; break; fi
done

if [ $# -gt 0 ]; then
  exec "$py" -m "$@" </dev/null
fi

status=0
run() {
  local name=$1; shift
  local out
  if out=$("$@" 2>&1 </dev/null); then
    echo "ok    $name"
  else
    status=2
    echo "FAIL  $name" >&2
    printf '%s\n' "$out" | tail -n 60 >&2
  fi
}

run "ruff check"   "$py" -m ruff check .
run "ruff format"  "$py" -m ruff format --check .
run "pyright"      "$py" -m pyright
run "bandit"       "$py" -m bandit -q -r cleanrr/ -ll
run "pytest"       "$py" -m pytest -q -p no:cacheprovider

if [ "$status" -ne 0 ]; then
  echo "gate: fix the failures above, re-run bash .claude/hooks/gate.sh, then finish." >&2
fi
exit "$status"
