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
