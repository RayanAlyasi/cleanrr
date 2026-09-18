#!/usr/bin/env bash
# PreToolUse guard for the Agent tool: allow spawning only the named subagent types.
#
#   bash .claude/hooks/allow-subagent.sh cleanrr-architect [more-types...]
#
# A type list in `tools: Agent(...)` is ignored inside a subagent definition, so
# this hook is what actually limits who a subagent may spawn.
# Reads the tool call JSON from stdin. Exit 2 blocks the call; stderr is the reason.
set -u

here=$(cd "$(dirname "$0")" && pwd)
python_bin=$(command -v python || command -v python3)
requested=$("$python_bin" "$here/hook_field.py" tool_input.subagent_type)
if [ $? -eq 3 ]; then
  echo "blocked: could not parse the tool input; refusing to guess the subagent type." >&2
  exit 2
fi

for allowed in "$@"; do
  [ "$requested" = "$allowed" ] && exit 0
done
echo "blocked: this agent may spawn only: $*. Requested: ${requested:-<unspecified>}." >&2
exit 2
