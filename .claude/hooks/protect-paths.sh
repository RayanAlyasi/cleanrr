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

input=""
if [ ! -t 0 ]; then
  input=$(cat)
fi
[ -z "$input" ] && exit 0
path=$(printf '%s' "$input" | python -c 'import json,sys
print(json.load(sys.stdin).get("tool_input", {}).get("file_path", ""))' 2>/dev/null) || {
  echo "blocked: could not parse the tool input; refusing to guess the path." >&2
  exit 2
}
[ -z "$path" ] && exit 0
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
