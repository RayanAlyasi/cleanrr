#!/usr/bin/env bash
# PreToolUse guard for the Agent tool: allow spawning only the named subagent types.
#
#   bash .claude/hooks/allow-subagent.sh cleanrr-architect [more-types...]
#
# A type list in `tools: Agent(...)` is ignored inside a subagent definition, so
# this hook is what actually limits who a subagent may spawn.
# Reads the tool call JSON from stdin. Exit 2 blocks the call; stderr is the reason.
set -u

input=""
if [ ! -t 0 ]; then
  input=$(cat)
fi
[ -z "$input" ] && exit 0
requested=$(printf '%s' "$input" | python -c 'import json,sys
print(json.load(sys.stdin).get("tool_input", {}).get("subagent_type", ""))' 2>/dev/null) || {
  echo "blocked: could not parse the tool input; refusing to guess the subagent type." >&2
  exit 2
}

for allowed in "$@"; do
  [ "$requested" = "$allowed" ] && exit 0
done
echo "blocked: this agent may spawn only: $*. Requested: ${requested:-<unspecified>}." >&2
exit 2
