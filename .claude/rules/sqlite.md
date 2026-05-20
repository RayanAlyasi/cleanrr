---
paths:
  - "cleanrr/identity.py"
  - "tests/test_identity.py"
---

# SQLite conventions — cleanrr

- Use `aiosqlite`. Never import `sqlite3` directly into async code.
- Every query uses parameterised `?` placeholders. No f-strings, no `%`, no string concat in SQL.
- Always `await conn.commit()` after writes. Reads don't need commit.
- Use `ON CONFLICT(...) DO UPDATE SET ...` for upserts. Don't read-then-write — it's a race.
- Timestamps stored as `INTEGER` (Unix epoch seconds), not ISO strings. Comparisons stay unambiguous.
- Create tables with `IF NOT EXISTS` in the `start()` method. No separate migration layer until ≥5 tables.
- Close connections in `stop()`. Don't rely on `__del__`.
- Reserve the `data/` directory for the SQLite file. Already volume-mounted in docker-compose.
