from __future__ import annotations

import logging
import secrets
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

    async def stop(self) -> None:
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None

    async def issue_code(self, overseerr_username: str) -> str:
        if self._conn is None:
            raise RuntimeError("Identity.start() must be called before issue_code()")
        now = _now_ts()
        code = generate_code()
        await self._conn.execute(
            "INSERT INTO link_codes (code, overseerr_username, created_at, expires_at)"
            " VALUES (?, ?, ?, ?)",
            (code, overseerr_username, now, now + int(self._code_ttl.total_seconds())),
        )
        await self._conn.commit()
        logger.info("issued link code for overseerr user @%s", overseerr_username)
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
            " RETURNING overseerr_username",
            (now, code, now),
        )
        row = await cursor.fetchone()
        if row is None:
            logger.info("link code redemption failed for telegram %s", telegram_user_id)
            metrics.link_codes_redeemed_total.labels(status="invalid").inc()
            return None
        overseerr_username = row[0]
        # ON CONFLICT replaces the previous mapping so re-linking just works.
        await self._conn.execute(
            "INSERT INTO user_links (telegram_user_id, overseerr_username, linked_at)"
            " VALUES (?, ?, ?)"
            " ON CONFLICT(telegram_user_id) DO UPDATE SET"
            " overseerr_username = excluded.overseerr_username,"
            " linked_at = excluded.linked_at",
            (telegram_user_id, overseerr_username, now),
        )
        await self._conn.commit()
        logger.info("linked telegram %s to overseerr @%s", telegram_user_id, overseerr_username)
        metrics.link_codes_redeemed_total.labels(status="success").inc()
        metrics.linked_users.set(await self.user_count())
        return overseerr_username

    # Used by Phase 4 tool handlers to resolve telegram_id → overseerr_username.
    async def get_link(self, telegram_user_id: int) -> str | None:
        if self._conn is None:
            raise RuntimeError("Identity.start() must be called before get_link()")
        cursor = await self._conn.execute(
            "SELECT overseerr_username FROM user_links WHERE telegram_user_id = ?",
            (telegram_user_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def user_count(self) -> int:
        if self._conn is None:
            raise RuntimeError("Identity.start() must be called before user_count()")
        cursor = await self._conn.execute("SELECT COUNT(*) FROM user_links")
        row = await cursor.fetchone()
        return int(row[0]) if row else 0
