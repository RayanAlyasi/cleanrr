import logging
from datetime import timedelta
from pathlib import Path

import aiosqlite
import pytest

import cleanrr.metrics as metrics
from cleanrr.identity import Identity, LinkedUser, generate_code

_LEGACY_SCHEMA = """
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
"""

_LEGACY_UPSERT = (
    "INSERT INTO user_links (telegram_user_id, overseerr_username, linked_at)"
    " VALUES (?, ?, ?)"
    " ON CONFLICT(telegram_user_id) DO UPDATE SET"
    " overseerr_username = excluded.overseerr_username,"
    " linked_at = excluded.linked_at"
)


def test_generate_code_format() -> None:
    code = generate_code()
    assert len(code) == 9
    assert code[4] == "-"
    valid_chars = set("ABCDEFGHJKMNPQRSTUVWXYZ23456789")
    assert all(c in valid_chars for c in code if c != "-")


def test_generate_code_uniqueness() -> None:
    codes = {generate_code() for _ in range(1000)}
    assert len(codes) == 1000


async def _store(tmp_path: Path, ttl: timedelta = timedelta(hours=24)) -> Identity:
    store = Identity(db_path=tmp_path / "test.db", code_ttl=ttl)
    await store.start()
    return store


async def _run_legacy_upsert(
    db_path: Path, telegram_user_id: int, username: str, linked_at: int
) -> None:
    conn = await aiosqlite.connect(db_path)
    try:
        await conn.execute(_LEGACY_UPSERT, (telegram_user_id, username, linked_at))
        await conn.commit()
    finally:
        await conn.close()


# issue_code now requires an overseerr_user_id, so it can no longer produce a
# link_codes row without one. This simulates a code issued by a pre-upgrade
# binary that never wrote the column, which redeem_code must still accept.
async def _run_legacy_code_insert(
    db_path: Path, code: str, username: str, created_at: int, expires_at: int
) -> None:
    conn = await aiosqlite.connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO link_codes (code, overseerr_username, created_at, expires_at)"
            " VALUES (?, ?, ?, ?)",
            (code, username, created_at, expires_at),
        )
        await conn.commit()
    finally:
        await conn.close()


async def test_issue_and_redeem(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        code = await store.issue_code("alice", overseerr_user_id=7)
        assert await store.redeem_code(code, telegram_user_id=12345) == "alice"
    finally:
        await store.stop()


async def test_redeem_invalid_code(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        assert await store.redeem_code("NOPE-NOPE", telegram_user_id=12345) is None
    finally:
        await store.stop()


async def test_redeem_consumed_code(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        code = await store.issue_code("alice", overseerr_user_id=7)
        await store.redeem_code(code, telegram_user_id=12345)
        assert await store.redeem_code(code, telegram_user_id=99999) is None
    finally:
        await store.stop()


async def test_redeem_expired_code(tmp_path: Path) -> None:
    # Negative TTL means codes are issued already-expired — exercises the expiry branch.
    store = await _store(tmp_path, ttl=timedelta(hours=-1))
    try:
        code = await store.issue_code("alice", overseerr_user_id=7)
        assert await store.redeem_code(code, telegram_user_id=12345) is None
    finally:
        await store.stop()


async def test_relink_overwrites(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        code_alice = await store.issue_code("alice", overseerr_user_id=7)
        await store.redeem_code(code_alice, telegram_user_id=12345)
        code_bob = await store.issue_code("bob", overseerr_user_id=9)
        assert await store.redeem_code(code_bob, telegram_user_id=12345) == "bob"
    finally:
        await store.stop()


async def test_get_linked_user_returns_none_when_unlinked(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        assert await store.get_linked_user(99999) is None
    finally:
        await store.stop()


async def test_start_creates_parent_directory(tmp_path: Path) -> None:
    nested = tmp_path / "data" / "subdir"
    store = Identity(db_path=nested / "test.db", code_ttl=timedelta(hours=1))
    await store.start()
    try:
        assert nested.exists()
    finally:
        await store.stop()


async def test_start_required_before_operations(tmp_path: Path) -> None:
    store = Identity(db_path=tmp_path / "test.db", code_ttl=timedelta(hours=1))
    with pytest.raises(RuntimeError, match="start"):
        await store.issue_code("alice", overseerr_user_id=7)


async def test_redeem_code_requires_start(tmp_path: Path) -> None:
    store = Identity(db_path=tmp_path / "test.db", code_ttl=timedelta(hours=1))
    with pytest.raises(RuntimeError, match="start"):
        await store.redeem_code("CODE-CODE", telegram_user_id=1)


async def test_get_linked_user_requires_start(tmp_path: Path) -> None:
    store = Identity(db_path=tmp_path / "test.db", code_ttl=timedelta(hours=1))
    with pytest.raises(RuntimeError, match="start"):
        await store.get_linked_user(1)


async def test_record_overseerr_user_id_requires_start(tmp_path: Path) -> None:
    store = Identity(db_path=tmp_path / "test.db", code_ttl=timedelta(hours=1))
    link = LinkedUser(
        telegram_user_id=1, overseerr_username="alice", linked_at=1000, overseerr_user_id=None
    )
    with pytest.raises(RuntimeError, match="start"):
        await store.record_overseerr_user_id(link, 7)


async def test_links_needing_overseerr_user_id_requires_start(tmp_path: Path) -> None:
    store = Identity(db_path=tmp_path / "test.db", code_ttl=timedelta(hours=1))
    with pytest.raises(RuntimeError, match="start"):
        await store.links_needing_overseerr_user_id()


async def test_count_links_needing_overseerr_user_id_requires_start(tmp_path: Path) -> None:
    store = Identity(db_path=tmp_path / "test.db", code_ttl=timedelta(hours=1))
    with pytest.raises(RuntimeError, match="start"):
        await store.count_links_needing_overseerr_user_id()


async def test_user_count_requires_start(tmp_path: Path) -> None:
    store = Identity(db_path=tmp_path / "test.db", code_ttl=timedelta(hours=1))
    with pytest.raises(RuntimeError, match="start"):
        await store.user_count()


async def test_start_twice_is_a_noop(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        await store.start()  # must not raise or reopen the connection
        assert await store.get_linked_user(1) is None
    finally:
        await store.stop()


async def test_stop_before_start_is_a_noop(tmp_path: Path) -> None:
    store = Identity(db_path=tmp_path / "test.db", code_ttl=timedelta(hours=1))
    await store.stop()  # must not raise


async def test_issue_code_logs_at_info(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    store = await _store(tmp_path)
    try:
        with caplog.at_level(logging.INFO, logger="cleanrr.identity"):
            code = await store.issue_code("alice", overseerr_user_id=7)
        messages = [r.getMessage() for r in caplog.records]
        assert any("issued link code for overseerr user @alice" in m for m in messages)
        assert all(code not in m for m in messages)
    finally:
        await store.stop()


async def test_redeem_code_logs_success(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    store = await _store(tmp_path)
    try:
        code = await store.issue_code("alice", overseerr_user_id=7)
        with caplog.at_level(logging.INFO, logger="cleanrr.identity"):
            await store.redeem_code(code, telegram_user_id=12345)
        messages = [r.getMessage() for r in caplog.records]
        assert any("linked telegram 12345 to overseerr @alice" in m for m in messages)
    finally:
        await store.stop()


async def test_redeem_code_logs_failure(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    store = await _store(tmp_path)
    try:
        with caplog.at_level(logging.INFO, logger="cleanrr.identity"):
            await store.redeem_code("NOPE-NOPE", telegram_user_id=12345)
        messages = [r.getMessage() for r in caplog.records]
        assert any("link code redemption failed for telegram 12345" in m for m in messages)
    finally:
        await store.stop()


async def test_user_count(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        assert await store.user_count() == 0
        code_a = await store.issue_code("alice", overseerr_user_id=7)
        code_b = await store.issue_code("bob", overseerr_user_id=9)
        assert await store.user_count() == 0
        await store.redeem_code(code_a, telegram_user_id=111)
        assert await store.user_count() == 1
        await store.redeem_code(code_b, telegram_user_id=222)
        assert await store.user_count() == 2
    finally:
        await store.stop()


async def test_issue_and_redeem_with_overseerr_user_id(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        code = await store.issue_code("alice", overseerr_user_id=7)
        assert await store.redeem_code(code, telegram_user_id=12345) == "alice"
        link = await store.get_linked_user(12345)
        assert link is not None
        assert link.telegram_user_id == 12345
        assert link.overseerr_username == "alice"
        assert link.overseerr_user_id == 7
        assert isinstance(link.linked_at, int)
    finally:
        await store.stop()


async def test_redeeming_a_code_without_an_id_leaves_link_usable(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        await _run_legacy_code_insert(
            tmp_path / "test.db", "LGCY-CODE", "alice", 1_000, 9_999_999_999
        )
        assert await store.redeem_code("LGCY-CODE", telegram_user_id=12345) == "alice"
        link = await store.get_linked_user(12345)
        assert link is not None
        assert link.overseerr_username == "alice"
        assert link.overseerr_user_id is None
    finally:
        await store.stop()


async def test_relink_to_different_user_replaces_overseerr_user_id(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        code_alice = await store.issue_code("alice", overseerr_user_id=7)
        await store.redeem_code(code_alice, telegram_user_id=12345)
        code_bob = await store.issue_code("bob", overseerr_user_id=9)
        assert await store.redeem_code(code_bob, telegram_user_id=12345) == "bob"
        link = await store.get_linked_user(12345)
        assert link is not None
        assert link.overseerr_username == "bob"
        assert link.overseerr_user_id == 9
    finally:
        await store.stop()


async def test_relink_without_overseerr_user_id_clears_stored_id(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        code_with_id = await store.issue_code("alice", overseerr_user_id=7)
        await store.redeem_code(code_with_id, telegram_user_id=12345)
        await _run_legacy_code_insert(
            tmp_path / "test.db", "LGCY-CODE", "alice", 1_000, 9_999_999_999
        )
        await store.redeem_code("LGCY-CODE", telegram_user_id=12345)
        link = await store.get_linked_user(12345)
        assert link is not None
        assert link.overseerr_user_id is None
    finally:
        await store.stop()


async def test_redeem_without_id_raises_missing_gauge(tmp_path: Path) -> None:
    metrics.links_missing_overseerr_user_id.set(0)
    store = await _store(tmp_path)
    try:
        await _run_legacy_code_insert(
            tmp_path / "test.db", "LGCY-CODE", "alice", 1_000, 9_999_999_999
        )
        await store.redeem_code("LGCY-CODE", telegram_user_id=12345)
        assert metrics.links_missing_overseerr_user_id._value.get() == 1  # type: ignore[attr-defined]
    finally:
        await store.stop()


async def test_redeem_with_id_lowers_missing_gauge(tmp_path: Path) -> None:
    metrics.links_missing_overseerr_user_id.set(0)
    store = await _store(tmp_path)
    try:
        await _run_legacy_code_insert(
            tmp_path / "test.db", "LGCY-CODE", "alice", 1_000, 9_999_999_999
        )
        await store.redeem_code("LGCY-CODE", telegram_user_id=12345)
        assert metrics.links_missing_overseerr_user_id._value.get() == 1  # type: ignore[attr-defined]

        code_with = await store.issue_code("alice", overseerr_user_id=7)
        await store.redeem_code(code_with, telegram_user_id=12345)
        assert metrics.links_missing_overseerr_user_id._value.get() == 0  # type: ignore[attr-defined]
    finally:
        await store.stop()


async def test_record_overseerr_user_id_lowers_missing_gauge(tmp_path: Path) -> None:
    metrics.links_missing_overseerr_user_id.set(0)
    store = await _store(tmp_path)
    try:
        await _run_legacy_code_insert(
            tmp_path / "test.db", "LGCY-CODE", "alice", 1_000, 9_999_999_999
        )
        await store.redeem_code("LGCY-CODE", telegram_user_id=12345)
        assert metrics.links_missing_overseerr_user_id._value.get() == 1  # type: ignore[attr-defined]

        link = await store.get_linked_user(12345)
        assert link is not None
        assert await store.record_overseerr_user_id(link, 7) is True
        assert metrics.links_missing_overseerr_user_id._value.get() == 0  # type: ignore[attr-defined]

        assert await store.record_overseerr_user_id(link, 9) is False
        assert metrics.links_missing_overseerr_user_id._value.get() == 0  # type: ignore[attr-defined]
    finally:
        await store.stop()


async def test_get_linked_user_treats_rollback_era_row_as_stale(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        code = await store.issue_code("alice", overseerr_user_id=7)
        await store.redeem_code(code, telegram_user_id=12345)

        await _run_legacy_upsert(tmp_path / "test.db", 12345, "bob", 999_999_999)

        link = await store.get_linked_user(12345)
        assert link is not None
        assert link.overseerr_username == "bob"
        assert link.overseerr_user_id is None

        needing = await store.links_needing_overseerr_user_id()
        assert any(row.telegram_user_id == 12345 for row in needing)
    finally:
        await store.stop()


async def test_record_overseerr_user_id_persists_and_rejects_second_write(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        await _run_legacy_upsert(tmp_path / "test.db", 12345, "alice", 1_000)
        link = await store.get_linked_user(12345)
        assert link is not None

        assert await store.record_overseerr_user_id(link, 7) is True
        assert await store.record_overseerr_user_id(link, 9) is False

        updated = await store.get_linked_user(12345)
        assert updated is not None
        assert updated.overseerr_user_id == 7
    finally:
        await store.stop()


async def test_record_overseerr_user_id_fails_when_linked_at_moved_on(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        code = await store.issue_code("alice", overseerr_user_id=7)
        await store.redeem_code(code, telegram_user_id=12345)
        link = await store.get_linked_user(12345)
        assert link is not None
        stale_link = LinkedUser(
            telegram_user_id=link.telegram_user_id,
            overseerr_username=link.overseerr_username,
            linked_at=link.linked_at - 1,
            overseerr_user_id=None,
        )

        assert await store.record_overseerr_user_id(stale_link, 9) is False

        current = await store.get_linked_user(12345)
        assert current is not None
        assert current.overseerr_user_id == 7
    finally:
        await store.stop()


async def test_record_overseerr_user_id_succeeds_when_stored_id_is_stale(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        code = await store.issue_code("alice", overseerr_user_id=7)
        await store.redeem_code(code, telegram_user_id=12345)

        # Simulate a rollback-era re-link: bumps linked_at without touching the
        # new columns, leaving the stored id stale.
        await _run_legacy_upsert(tmp_path / "test.db", 12345, "alice", 999_999_999)

        link = await store.get_linked_user(12345)
        assert link is not None
        assert link.overseerr_user_id is None

        assert await store.record_overseerr_user_id(link, 11) is True

        updated = await store.get_linked_user(12345)
        assert updated is not None
        assert updated.overseerr_user_id == 11
    finally:
        await store.stop()


async def test_links_needing_overseerr_user_id_and_count(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        await _run_legacy_upsert(tmp_path / "test.db", 111, "alice", 1_000)
        await _run_legacy_upsert(tmp_path / "test.db", 222, "alice", 1_000)

        needing = await store.links_needing_overseerr_user_id()
        assert {row.telegram_user_id for row in needing} == {111, 222}
        assert all(row.overseerr_user_id is None for row in needing)
        assert await store.count_links_needing_overseerr_user_id() == len(needing) == 2

        link_a = await store.get_linked_user(111)
        link_b = await store.get_linked_user(222)
        assert link_a is not None
        assert link_b is not None
        assert await store.record_overseerr_user_id(link_a, 7) is True
        assert await store.record_overseerr_user_id(link_b, 8) is True

        assert await store.links_needing_overseerr_user_id() == []
        assert await store.count_links_needing_overseerr_user_id() == 0
    finally:
        await store.stop()


async def test_links_needing_overseerr_user_id_includes_stale_id(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        code = await store.issue_code("alice", overseerr_user_id=7)
        await store.redeem_code(code, telegram_user_id=12345)

        await _run_legacy_upsert(tmp_path / "test.db", 12345, "alice", 999_999_999)

        needing = await store.links_needing_overseerr_user_id()
        assert any(row.telegram_user_id == 12345 for row in needing)
        assert await store.count_links_needing_overseerr_user_id() == len(needing)
    finally:
        await store.stop()


async def test_start_twice_with_new_columns_present_does_not_raise(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    first = Identity(db_path=db_path, code_ttl=timedelta(hours=1))
    await first.start()
    await first.stop()

    second = Identity(db_path=db_path, code_ttl=timedelta(hours=1))
    await second.start()
    try:
        assert await second.get_linked_user(1) is None
    finally:
        await second.stop()


async def test_start_migrates_pre_change_schema_and_keeps_legacy_row(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    conn = await aiosqlite.connect(db_path)
    try:
        await conn.executescript(_LEGACY_SCHEMA)
        await conn.execute(
            "INSERT INTO user_links (telegram_user_id, overseerr_username, linked_at)"
            " VALUES (?, ?, ?)",
            (12345, "alice", 1000),
        )
        await conn.commit()
    finally:
        await conn.close()

    store = Identity(db_path=db_path, code_ttl=timedelta(hours=1))
    await store.start()
    try:
        link = await store.get_linked_user(12345)
        assert link is not None
        assert link.overseerr_username == "alice"
        assert link.overseerr_user_id is None
    finally:
        await store.stop()


async def test_start_completes_partially_applied_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    conn = await aiosqlite.connect(db_path)
    try:
        await conn.executescript(_LEGACY_SCHEMA)
        await conn.execute("ALTER TABLE link_codes ADD COLUMN overseerr_user_id INTEGER")
        await conn.commit()
    finally:
        await conn.close()

    store = Identity(db_path=db_path, code_ttl=timedelta(hours=1))
    await store.start()
    try:
        code = await store.issue_code("alice", overseerr_user_id=7)
        assert await store.redeem_code(code, telegram_user_id=12345) == "alice"
        link = await store.get_linked_user(12345)
        assert link is not None
        assert link.overseerr_username == "alice"
        assert link.overseerr_user_id == 7
    finally:
        await store.stop()
