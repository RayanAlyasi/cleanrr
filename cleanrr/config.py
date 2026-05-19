import os
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    telegram_bot_token: SecretStr = Field(..., description="Token from @BotFather")

    claude_code_oauth_token: SecretStr | None = Field(
        default=None,
        description="OAuth token from `claude setup-token` — bills against Claude.ai subscription",
    )
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        description="Anthropic API key — pay-per-token alternative to the OAuth token",
    )

    claude_model: str = Field(
        default="sonnet",
        description="Shorthand (opus/sonnet/haiku) or full model ID like claude-sonnet-4-6",
    )
    claude_system_prompt: str | None = Field(
        default=None,
        description="Override the built-in bot persona. Empty means use the default.",
    )

    admin_telegram_ids: set[int] = Field(
        default_factory=set,
        description="Telegram user IDs allowed to run /invite; empty disables /invite.",
    )
    database_path: Path = Field(
        default=Path("data/cleanrr.db"),
        description="SQLite path for link codes and Telegram↔Overseerr mappings.",
    )
    link_code_ttl_hours: int = Field(
        default=24,
        description="How long link codes remain valid before expiring",
        gt=0,
    )

    log_level: str = Field(default="INFO")

    @field_validator("admin_telegram_ids", mode="before")
    @classmethod
    def _parse_csv_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return {int(x.strip()) for x in value.split(",") if x.strip()}
        return value

    @model_validator(mode="after")
    def _require_one_auth_method(self) -> Self:
        if self.claude_code_oauth_token is None and self.anthropic_api_key is None:
            raise ValueError("Set either CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .env")
        return self


def export_sdk_credentials(settings: Settings) -> None:
    """Propagate auth tokens into os.environ so the Agent SDK's bundled CLI can read them.

    Pydantic-settings loads .env into our Settings object but does not write back
    to os.environ, while the SDK subprocess only sees the latter.
    """
    if settings.claude_code_oauth_token is not None:
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = settings.claude_code_oauth_token.get_secret_value()
    if settings.anthropic_api_key is not None:
        os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key.get_secret_value()
