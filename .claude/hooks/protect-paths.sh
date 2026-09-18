#!/usr/bin/env bash
# PreToolUse guard for Write|Edit, per agent role.
#
#   bash .claude/hooks/protect-paths.sh coder      block release-please-owned files
#   bash .claude/hooks/protect-paths.sh planner    allow only .claude/plans/*.md
#   bash .claude/hooks/protect-paths.sh reviewer   allow only the agent's memory dir
#
# Reads the tool call JSON from stdin. Exit 2 blocks the call; stderr is the reason.
set -u
mode=${1:-coder}

here=$(cd "$(dirname "$0")" && pwd)
python_bin=$(command -v python || command -v python3)
path=$("$python_bin" "$here/hook_field.py" tool_input.file_path)
if [ $? -eq 3 ]; then
  echo "blocked: could not parse the tool input; refusing to guess the path." >&2
  exit 2
fi
[ -z "$path" ] && exit 0
# Windows separators to forward slashes (pattern is one escaped backslash).
norm=${path//\\//}
base=${norm##*/}

case $mode in
  coder)
    case $base in
      CHANGELOG.md|.release-please-manifest.json|.release-please-config.json)
        echo "blocked: $base is owned by release-please; land a feat:/fix: commit instead." >&2
        exit 2 ;;
    esac ;;
  planner)
    case $norm in
      *.claude/plans/*.md) exit 0 ;;
    esac
    echo "blocked: the planner writes only .claude/plans/<slug>.md, never code or docs." >&2
    exit 2 ;;
  reviewer)
    case $norm in
      *.claude/agent-memory/*|*.claude/agent-memory-local/*) exit 0 ;;
    esac
    echo "blocked: review agents are read-only outside their own memory directory." >&2
    exit 2 ;;
esac
exit 0
