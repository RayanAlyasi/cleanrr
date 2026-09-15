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
input=""
if [ ! -t 0 ]; then
  input=$(cat)
fi
if [ -n "$input" ]; then
  dir=$(printf '%s' "$input" | python -c 'import json,sys
print(json.load(sys.stdin).get("cwd", ""))' 2>/dev/null) || {
    echo "gate: could not parse hook input; refusing to guess which checkout to test." >&2
    exit 2
  }
  if [ -n "$dir" ]; then
    command -v cygpath >/dev/null 2>&1 && dir=$(cygpath -u "$dir")
    cd "$dir" || { echo "gate: cannot cd to $dir" >&2; exit 2; }
  fi
fi

# Worktrees share .git but not .venv, so resolve the interpreter from the main
# checkout. Fall back to whatever python is on PATH.
main=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
main=${main%/.git}
py=python
for cand in "$main/.venv/Scripts/python.exe" "$main/.venv/bin/python"; do
  if [ -x "$cand" ]; then py=$cand; break; fi
done

if [ $# -gt 0 ]; then
  exec "$py" -m "$@"
fi

status=0
run() {
  local name=$1; shift
  local out
  if out=$("$@" 2>&1); then
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
