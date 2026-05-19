from datetime import timedelta
from pathlib import Path

import pytest

from cleanrr.identity import Identity, generate_code


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


async def test_issue_and_redeem(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        code = await store.issue_code("alice")
        assert await store.redeem_code(code, telegram_user_id=12345) == "alice"
        assert await store.get_link(12345) == "alice"
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
        code = await store.issue_code("alice")
        await store.redeem_code(code, telegram_user_id=12345)
        assert await store.redeem_code(code, telegram_user_id=99999) is None
    finally:
        await store.stop()


async def test_redeem_expired_code(tmp_path: Path) -> None:
    # Negative TTL means codes are issued already-expired — exercises the expiry branch.
    store = await _store(tmp_path, ttl=timedelta(hours=-1))
    try:
        code = await store.issue_code("alice")
        assert await store.redeem_code(code, telegram_user_id=12345) is None
    finally:
        await store.stop()


async def test_relink_overwrites(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        code_alice = await store.issue_code("alice")
        await store.redeem_code(code_alice, telegram_user_id=12345)
        code_bob = await store.issue_code("bob")
        assert await store.redeem_code(code_bob, telegram_user_id=12345) == "bob"
        assert await store.get_link(12345) == "bob"
    finally:
        await store.stop()


async def test_get_link_returns_none_when_unlinked(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        assert await store.get_link(99999) is None
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


def test_start_required_before_operations(tmp_path: Path) -> None:
    store = Identity(db_path=tmp_path / "test.db", code_ttl=timedelta(hours=1))
    with pytest.raises(RuntimeError, match="start"):
        import asyncio

        asyncio.run(store.issue_code("alice"))
