---
paths:
  - "cleanrr/**/*.py"
  - "tests/**/*.py"
---

# Python style — cleanrr

- Type hints on every function signature. No unexplained `Any`.
- Default to **no comments**. Add one only when the *why* is non-obvious (workaround, hidden constraint, subtle invariant). Never restate the diff in prose.
- Names are self-documenting. No `mgr`, `hdlr`, `tmp`, or `utils.py` dumping grounds.
- Modules are single-responsibility. If a file grows past ~150 lines, propose splitting.
- Prefer composition over inheritance.
- No mutable default arguments.
- No broad `except:` — catch the specific exception you handle.
- All I/O in async handlers must be async (no sync `sqlite3`, no `requests`, no `time.sleep`).
- Tests live in `tests/`. One test file per source module is the default shape.
