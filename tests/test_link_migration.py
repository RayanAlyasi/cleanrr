from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from cleanrr.config import Settings
from cleanrr.identity import Identity, LinkedUser
from cleanrr.link_migration import BackfillResult, backfill_overseerr_user_ids


def _settings(**overrides: object) -> Settings:
    base = {
        "telegram_bot_token": "test_token",
        "anthropic_api_key": "sk-test",
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _link(telegram_user_id: int, overseerr_username: str, linked_at: int = 1000) -> LinkedUser:
    return LinkedUser(
        telegram_user_id=telegram_user_id,
        overseerr_username=overseerr_username,
        linked_at=linked_at,
        overseerr_user_id=None,
    )


@pytest.fixture
def mock_identity() -> MagicMock:
    identity = MagicMock(spec=Identity)
    identity.links_needing_overseerr_user_id = AsyncMock(return_value=[])
    identity.record_overseerr_user_id = AsyncMock(return_value=True)
    return identity


@pytest.fixture
def mock_client() -> AsyncMock:
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def settings() -> Settings:
    return _settings(overseerr_url="http://overseerr:5055", overseerr_api_key="test_key")


@pytest.mark.asyncio
async def test_nothing_pending_does_no_work(
    mock_identity: MagicMock,
    mock_client: AsyncMock,
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="cleanrr.link_migration"):
        result = await backfill_overseerr_user_ids(mock_identity, mock_client, settings)

    assert result == BackfillResult(0, 0)
    mock_client.get.assert_not_awaited()
    mock_identity.record_overseerr_user_id.assert_not_awaited()
    assert "link migration" not in caplog.text


@pytest.mark.asyncio
async def test_overseerr_unconfigured_leaves_rows_pending(
    mock_identity: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_identity.links_needing_overseerr_user_id = AsyncMock(return_value=[_link(1, "alice")])
    unconfigured = _settings(overseerr_url=None, overseerr_api_key=None)

    with caplog.at_level(logging.WARNING, logger="cleanrr.link_migration"):
        result = await backfill_overseerr_user_ids(mock_identity, None, unconfigured)

    assert result == BackfillResult(0, 1)
    mock_identity.record_overseerr_user_id.assert_not_awaited()
    assert "link migration skipped" in caplog.text
    assert "1 link" in caplog.text


@pytest.mark.asyncio
async def test_shared_username_costs_one_lookup(
    mock_identity: MagicMock,
    mock_client: AsyncMock,
    settings: Settings,
) -> None:
    mock_identity.links_needing_overseerr_user_id = AsyncMock(
        return_value=[_link(1, "alice"), _link(2, "alice")]
    )

    with patch(
        "cleanrr.link_migration._resolve_user_id", AsyncMock(return_value=(42, "ok"))
    ) as mock_resolve:
        result = await backfill_overseerr_user_ids(mock_identity, mock_client, settings)

    mock_resolve.assert_awaited_once()
    assert mock_identity.record_overseerr_user_id.await_count == 2
    assert result == BackfillResult(2, 0)


@pytest.mark.asyncio
async def test_unresolvable_username_stays_pending(
    mock_identity: MagicMock,
    mock_client: AsyncMock,
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_identity.links_needing_overseerr_user_id = AsyncMock(return_value=[_link(1, "alice")])

    with (
        caplog.at_level(logging.WARNING, logger="cleanrr.link_migration"),
        patch(
            "cleanrr.link_migration._resolve_user_id",
            AsyncMock(return_value=(None, "http_error")),
        ),
    ):
        result = await backfill_overseerr_user_ids(mock_identity, mock_client, settings)

    mock_identity.record_overseerr_user_id.assert_not_awaited()
    assert result == BackfillResult(0, 1)
    assert "@alice" in caplog.text
    assert "http_error" in caplog.text
    assert "stay on username lookup" in caplog.text
    assert "re-invited" not in caplog.text


@pytest.mark.asyncio
async def test_ambiguous_username_tells_operator_to_reinvite(
    mock_identity: MagicMock,
    mock_client: AsyncMock,
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_identity.links_needing_overseerr_user_id = AsyncMock(return_value=[_link(1, "alice")])

    with (
        caplog.at_level(logging.WARNING, logger="cleanrr.link_migration"),
        patch(
            "cleanrr.link_migration._resolve_user_id",
            AsyncMock(return_value=(None, "user_not_found")),
        ),
    ):
        result = await backfill_overseerr_user_ids(mock_identity, mock_client, settings)

    mock_identity.record_overseerr_user_id.assert_not_awaited()
    assert result == BackfillResult(0, 1)
    assert "no single overseerr user matches @alice exactly" in caplog.text
    assert "re-invited" in caplog.text
    assert "stay on username lookup" not in caplog.text


@pytest.mark.asyncio
async def test_resolve_raising_does_not_propagate(
    mock_identity: MagicMock,
    mock_client: AsyncMock,
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_identity.links_needing_overseerr_user_id = AsyncMock(return_value=[_link(1, "alice")])

    with (
        caplog.at_level(logging.WARNING, logger="cleanrr.link_migration"),
        patch(
            "cleanrr.link_migration._resolve_user_id",
            AsyncMock(side_effect=RuntimeError("boom")),
        ),
    ):
        result = await backfill_overseerr_user_ids(mock_identity, mock_client, settings)

    assert result == BackfillResult(0, 1)
    assert any(r.levelno == logging.WARNING for r in caplog.records)


@pytest.mark.asyncio
async def test_mixed_batch_resolves_one_and_leaves_one(
    mock_identity: MagicMock,
    mock_client: AsyncMock,
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_identity.links_needing_overseerr_user_id = AsyncMock(
        return_value=[_link(1, "alice"), _link(2, "bob")]
    )

    async def _resolve(
        _client: httpx.AsyncClient, _base_url: str, username: str
    ) -> tuple[int | None, str]:
        return (42, "ok") if username == "alice" else (None, "http_error")

    with (
        caplog.at_level(logging.INFO, logger="cleanrr.link_migration"),
        patch("cleanrr.link_migration._resolve_user_id", _resolve),
    ):
        result = await backfill_overseerr_user_ids(mock_identity, mock_client, settings)

    assert result == BackfillResult(1, 1)
    assert "couldn't resolve overseerr user @bob" in caplog.text
    assert "keyed 1 link(s) by overseerr user id, 1 still unresolved" in caplog.text


@pytest.mark.asyncio
async def test_concurrent_relink_does_not_count_as_migrated(
    mock_identity: MagicMock,
    mock_client: AsyncMock,
    settings: Settings,
) -> None:
    mock_identity.links_needing_overseerr_user_id = AsyncMock(return_value=[_link(1, "alice")])
    mock_identity.record_overseerr_user_id = AsyncMock(return_value=False)

    with patch("cleanrr.link_migration._resolve_user_id", AsyncMock(return_value=(42, "ok"))):
        result = await backfill_overseerr_user_ids(mock_identity, mock_client, settings)

    assert result == BackfillResult(0, 1)


@pytest.mark.asyncio
async def test_second_run_with_nothing_left_issues_no_lookup(
    mock_identity: MagicMock,
    mock_client: AsyncMock,
    settings: Settings,
) -> None:
    mock_identity.links_needing_overseerr_user_id = AsyncMock(return_value=[_link(1, "alice")])
    with patch(
        "cleanrr.link_migration._resolve_user_id", AsyncMock(return_value=(42, "ok"))
    ) as mock_resolve:
        first = await backfill_overseerr_user_ids(mock_identity, mock_client, settings)
        assert first == BackfillResult(1, 0)

        mock_identity.links_needing_overseerr_user_id = AsyncMock(return_value=[])
        mock_resolve.reset_mock()
        second = await backfill_overseerr_user_ids(mock_identity, mock_client, settings)

        assert second == BackfillResult(0, 0)
        mock_resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_username_control_chars_and_length_are_sanitized_in_the_log(
    mock_identity: MagicMock,
    mock_client: AsyncMock,
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    username = "ali\nce\x1b[31m" + "x" * 100
    mock_identity.links_needing_overseerr_user_id = AsyncMock(return_value=[_link(1, username)])

    with (
        caplog.at_level(logging.WARNING, logger="cleanrr.link_migration"),
        patch(
            "cleanrr.link_migration._resolve_user_id",
            AsyncMock(return_value=(None, "http_error")),
        ),
    ):
        await backfill_overseerr_user_ids(mock_identity, mock_client, settings)

    assert all(c.isprintable() for record in caplog.records for c in record.getMessage())
    expected = "".join(c for c in username if c.isprintable())[:32]
    assert expected in caplog.text
    assert "x" * 100 not in caplog.text
