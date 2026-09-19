from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite

import cleanrr.metrics as metrics

logger = logging.getLogger(__name__)

# Crockford-style alphabet — omits 0/O/1/I/L to keep codes unambiguous when
# read aloud or transcribed from a phone screen.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 8


def generate_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
    return f"{raw[:4]}-{raw[4:]}"


def _now_ts() -> int:
    return int(datetime.now(UTC).timestamp())


@dataclass(frozen=True)
class LinkedUser:
    telegram_user_id: int
    overseerr_username: str
    linked_at: int
    overseerr_user_id: int | None


# ALTER TABLE has no IF NOT EXISTS form, so each addition is guarded by a
# pragma_table_info check instead (see Identity._apply_schema_additions).
_SCHEMA_ADDITIONS: tuple[tuple[str, str, str], ...] = (
    (
        "link_codes",
        "overseerr_user_id",
        "ALTER TABLE link_codes ADD COLUMN overseerr_user_id INTEGER",
    ),
    (
        "user_links",
        "overseerr_user_id",
        "ALTER TABLE user_links ADD COLUMN overseerr_user_id INTEGER",
    ),
    (
        "user_links",
        "overseerr_user_id_linked_at",
        "ALTER TABLE user_links ADD COLUMN overseerr_user_id_linked_at INTEGER",
    ),
)


class Identity:
    """SQLite store for one-time link codes and confirmed Telegram → Overseerr mappings."""

    def __init__(self, db_path: Path, code_ttl: timedelta) -> None:
        self._db_path = db_path
        self._code_ttl = code_ttl
        self._conn: aiosqlite.Connection | None = None

    async def start(self) -> None:
        if self._conn is not None:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS link_codes (
                code TEXT PRIMARY KEY,
                overseerr_username TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                consumed_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS user_links (
                telegram_user_id INTEGER PRIMARY KEY,
                overseerr_username TEXT NOT NULL,
                linked_at INTEGER NOT NULL
            );
        """)
        await self._conn.commit()
        await self._apply_schema_additions(self._conn)

    async def _apply_schema_additions(self, conn: aiosqlite.Connection) -> None:
        for table, column, ddl in _SCHEMA_ADDITIONS:
            cursor = await conn.execute(
                "SELECT 1 FROM pragma_table_info(?) WHERE name = ?", (table, column)
            )
            row = await cursor.fetchone()
            if row is None:
                await conn.execute(ddl)
        await conn.commit()

    async def stop(self) -> None:
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None

    async def issue_code(self, overseerr_username: str, *, overseerr_user_id: int) -> str:
        if self._conn is None:
            raise RuntimeError("Identity.start() must be called before issue_code()")
        now = _now_ts()
        code = generate_code()
        await self._conn.execute(
            "INSERT INTO link_codes"
            " (code, overseerr_username, created_at, expires_at, overseerr_user_id)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                code,
                overseerr_username,
                now,
                now + int(self._code_ttl.total_seconds()),
                overseerr_user_id,
            ),
        )
        await self._conn.commit()
        # Strip newlines so a hostile username can't inject fake log lines.
        safe_username = overseerr_username.replace("\n", " ").replace("\r", " ")
        logger.info("issued link code for overseerr user @%s", safe_username)
        metrics.link_codes_issued_total.inc()
        return code

    async def redeem_code(self, code: str, telegram_user_id: int) -> str | None:
        if self._conn is None:
            raise RuntimeError("Identity.start() must be called before redeem_code()")
        now = _now_ts()
        # Atomic check-and-consume: a concurrent /link with the same code can't
        # win this UPDATE twice — the second attempt's WHERE clause fails on
        # consumed_at IS NULL. SELECT-then-UPDATE would race here.
        cursor = await self._conn.execute(
            "UPDATE link_codes SET consumed_at = ?"
            " WHERE code = ? AND consumed_at IS NULL AND expires_at > ?"
            " RETURNING overseerr_username, overseerr_user_id",
            (now, code, now),
        )
        row = await cursor.fetchone()
        if row is None:
            logger.info("link code redemption failed for telegram %s", telegram_user_id)
            metrics.link_codes_redeemed_total.labels(status="invalid").inc()
            return None
        overseerr_username = row[0]
        overseerr_user_id = row[1] if isinstance(row[1], int) else None
        # ON CONFLICT replaces the previous mapping so re-linking just works.
        # overseerr_user_id is assigned even when NULL: re-linking must never
        # leave a previous account's id behind.
        await self._conn.execute(
            "INSERT INTO user_links (telegram_user_id, overseerr_username, linked_at,"
            " overseerr_user_id, overseerr_user_id_linked_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(telegram_user_id) DO UPDATE SET"
            " overseerr_username = excluded.overseerr_username,"
            " linked_at = excluded.linked_at,"
            " overseerr_user_id = excluded.overseerr_user_id,"
            " overseerr_user_id_linked_at = excluded.overseerr_user_id_linked_at",
            (telegram_user_id, overseerr_username, now, overseerr_user_id, now),
        )
        await self._conn.commit()
        safe_username = overseerr_username.replace("\n", " ").replace("\r", " ")
        logger.info("linked telegram %s to overseerr @%s", telegram_user_id, safe_username)
        metrics.link_codes_redeemed_total.labels(status="success").inc()
        metrics.linked_users.set(await self.user_count())
        metrics.links_missing_overseerr_user_id.set(
            await self.count_links_needing_overseerr_user_id()
        )
        return overseerr_username

    async def get_linked_user(self, telegram_user_id: int) -> LinkedUser | None:
        if self._conn is None:
            raise RuntimeError("Identity.start() must be called before get_linked_user()")
        cursor = await self._conn.execute(
            "SELECT telegram_user_id, overseerr_username, linked_at,"
            " overseerr_user_id, overseerr_user_id_linked_at"
            " FROM user_links WHERE telegram_user_id = ?",
            (telegram_user_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        telegram_id, overseerr_username, linked_at, stored_id, stored_id_linked_at = row
        # An image without these columns only ever rewrites linked_at, never the
        # snapshot, so a mismatch means the stored id predates the current link.
        overseerr_user_id = (
            stored_id if isinstance(stored_id, int) and stored_id_linked_at == linked_at else None
        )
        return LinkedUser(
            telegram_user_id=telegram_id,
            overseerr_username=overseerr_username,
            linked_at=linked_at,
            overseerr_user_id=overseerr_user_id,
        )

    async def record_overseerr_user_id(self, link: LinkedUser, overseerr_user_id: int) -> bool:
        if self._conn is None:
            raise RuntimeError("Identity.start() must be called before record_overseerr_user_id()")
        cursor = await self._conn.execute(
            "UPDATE user_links"
            " SET overseerr_user_id = ?, overseerr_user_id_linked_at = ?"
            " WHERE telegram_user_id = ? AND overseerr_username = ? AND linked_at = ?"
            " AND (overseerr_user_id IS NULL OR overseerr_user_id_linked_at IS NOT linked_at)",
            (
                overseerr_user_id,
                link.linked_at,
                link.telegram_user_id,
                link.overseerr_username,
                link.linked_at,
            ),
        )
        await self._conn.commit()
        if cursor.rowcount == 1:
            metrics.links_missing_overseerr_user_id.set(
                await self.count_links_needing_overseerr_user_id()
            )
            return True
        return False

    async def links_needing_overseerr_user_id(self) -> list[LinkedUser]:
        if self._conn is None:
            raise RuntimeError(
                "Identity.start() must be called before links_needing_overseerr_user_id()"
            )
        cursor = await self._conn.execute(
            "SELECT telegram_user_id, overseerr_username, linked_at,"
            " overseerr_user_id, overseerr_user_id_linked_at"
            " FROM user_links"
            " WHERE overseerr_user_id IS NULL OR overseerr_user_id_linked_at IS NOT linked_at"
            " ORDER BY telegram_user_id"
        )
        rows = await cursor.fetchall()
        return [
            LinkedUser(
                telegram_user_id=row[0],
                overseerr_username=row[1],
                linked_at=row[2],
                overseerr_user_id=None,
            )
            for row in rows
        ]

    async def count_links_needing_overseerr_user_id(self) -> int:
        if self._conn is None:
            raise RuntimeError(
                "Identity.start() must be called before count_links_needing_overseerr_user_id()"
            )
        cursor = await self._conn.execute(
            "SELECT COUNT(*) FROM user_links"
            " WHERE overseerr_user_id IS NULL OR overseerr_user_id_linked_at IS NOT linked_at"
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def user_count(self) -> int:
        if self._conn is None:
            raise RuntimeError("Identity.start() must be called before user_count()")
        cursor = await self._conn.execute("SELECT COUNT(*) FROM user_links")
        row = await cursor.fetchone()
        return int(row[0]) if row else 0
