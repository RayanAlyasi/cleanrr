import pytest
from pydantic import ValidationError

from cleanrr.config import Settings


def _settings() -> Settings:
    # _env_file=None prevents pydantic-settings from picking up the dev .env when
    # tests run from the project root.
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_oauth_only_validates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-bot-token")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "fake-oauth")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    settings = _settings()

    assert settings.claude_code_oauth_token is not None
    assert settings.anthropic_api_key is None
    assert settings.claude_model == "sonnet"


def test_api_key_only_validates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-bot-token")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")

    settings = _settings()

    assert settings.anthropic_api_key is not None
    assert settings.claude_code_oauth_token is None


def test_missing_auth_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-bot-token")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(ValidationError):
        _settings()


def test_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-bot-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setenv("CLAUDE_MODEL", "haiku")

    assert _settings().claude_model == "haiku"


def test_claude_timeout_seconds_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-bot-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.delenv("CLAUDE_TIMEOUT_SECONDS", raising=False)

    assert _settings().claude_timeout_seconds == 120.0


def test_confirmation_ttl_must_be_under_claude_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-bot-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setenv("CLAUDE_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("CONFIRMATION_TTL_SECONDS", "60")
    with pytest.raises(ValueError, match="CONFIRMATION_TTL_SECONDS"):
        _settings()


def test_telegram_max_message_chars_default_is_2000(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-bot-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.delenv("TELEGRAM_MAX_MESSAGE_CHARS", raising=False)

    assert _settings().telegram_max_message_chars == 2000


def test_claude_timeout_seconds_rejects_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-bot-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setenv("CLAUDE_TIMEOUT_SECONDS", "0")

    with pytest.raises(ValidationError):
        _settings()


def test_telegram_max_message_chars_rejects_over_4096(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-bot-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setenv("TELEGRAM_MAX_MESSAGE_CHARS", "4097")

    with pytest.raises(ValidationError):
        _settings()


def test_admin_telegram_ids_single_value_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single bare ID is valid JSON (a number), which previously bypassed the
    CSV-split validator and reached pydantic as an int instead of a set."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-bot-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "6322869612")

    assert _settings().admin_telegram_ids == {6322869612}


def test_admin_telegram_ids_multiple_values_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-bot-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "123,456")

    assert _settings().admin_telegram_ids == {123, 456}


def test_admin_telegram_ids_empty_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-bot-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.delenv("ADMIN_TELEGRAM_IDS", raising=False)

    assert _settings().admin_telegram_ids == set()


def test_metrics_bind_address_default_is_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-bot-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.delenv("METRICS_BIND_ADDRESS", raising=False)

    assert str(_settings().metrics_bind_address) == "127.0.0.1"


def test_clear_sdk_credentials_removes_both_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from cleanrr.config import clear_sdk_credentials

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "fake-oauth")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")

    clear_sdk_credentials()

    assert "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_clear_sdk_credentials_is_idempotent() -> None:
    from cleanrr.config import clear_sdk_credentials

    clear_sdk_credentials()
    clear_sdk_credentials()
