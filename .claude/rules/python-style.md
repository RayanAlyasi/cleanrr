---
paths:
  - "cleanrr/**/*.py"
  - "tests/**/*.py"
---

# Python style — cleanrr

Checklist form. Rationale and examples live in `CODE_STANDARDS.md`; what gets built and why lives in `DESIGN_PRINCIPLES.md`.

- Type hints on every function signature. No unexplained `Any`; narrow JSON at the first `isinstance`.
- Default to **no comments**. Add one only when the *why* is non-obvious (workaround, hidden constraint, subtle invariant). Never restate the diff in prose. No `Args:`/`Returns:` docstring blocks.
- Names are self-documenting. No `mgr`, `hdlr`, `tmp`, or `utils.py` dumping grounds.
- Modules are single-responsibility. If a file grows past ~150 lines, propose splitting.
- Prefer composition over inheritance.
- No mutable default arguments.
- No broad `except:` — catch the specific exception you handle. Tools return `text_result(..., is_error=...)`; they never raise into the SDK.
- All I/O in async handlers must be async (no sync `sqlite3`, no `requests`, no `time.sleep`). Every `httpx` call has a timeout; every await that can outlive its caller is bounded.
- `Counter` for events, `Gauge` for state. Label values come from a `Literal` or constant, never from user or upstream data. Instrument symmetric paths symmetrically.
- Tests live in `tests/`, one file per source module. HTTP is mocked with `AsyncMock(spec=httpx.AsyncClient)`. Assert the identifiers a follow-up tool needs, not just a substring of the title. Every live-found bug gets a regression test.
- No throwaway files in the tree (`test.py`, `scratch.py`, `old_bot.py`). If it's not the real thing, it doesn't exist.
- Anything an end-user might reasonably want to change goes in `.env` / `Settings` with `.env.example` and README rows, not as a hardcoded constant. Don't expose internals like locks or retry counts.
- When adding a dependency, pin to the current stable version (check PyPI). Dependabot keeps it updated from there.
