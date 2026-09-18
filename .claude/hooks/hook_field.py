"""Print one field of a Claude Code hook's stdin JSON, e.g. ``tool_input.file_path``.

Exit 0 with the value on stdout, or with nothing when stdin is a terminal, empty,
silent, or lacks the field. Exit 3 when stdin carried data that is not valid JSON,
so callers can fail closed.

The read is bounded because a caller that leaves stdin open and silent must not
hang a hook, and it is done here rather than with bash ``read`` because bash reads
a pipe one byte at a time, which is too slow for a large Write payload.
"""

import json
import os
import sys
import threading

_WAIT_SECONDS = 3.0


def _drain(chunks: list[bytes]) -> None:
    while chunk := os.read(0, 65536):
        chunks.append(chunk)


def main() -> int:
    chunks: list[bytes] = []
    if not sys.stdin.isatty():
        reader = threading.Thread(target=_drain, args=(chunks,), daemon=True)
        reader.start()
        reader.join(_WAIT_SECONDS)
    raw = b"".join(chunks).strip()
    if not raw:
        return 0
    try:
        value: object = json.loads(raw)
    except ValueError:
        return 3
    for key in sys.argv[1].split("."):
        value = value.get(key, "") if isinstance(value, dict) else ""
    sys.stdout.buffer.write(str(value).encode("utf-8"))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    # os._exit: the reader thread may still be blocked on a silent stdin.
    os._exit(main())
