import os
from pathlib import Path
from typing import Self

from pydantic import Field, HttpUrl, IPvAnyAddress, SecretStr, field_validator, model_validator
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

    metrics_enabled: bool = Field(
        default=False,
        description="Expose Prometheus metrics on METRICS_PORT.",
    )
    metrics_port: int = Field(
        default=9100,
        gt=0,
        lt=65536,
        description="Port for the Prometheus metrics HTTP endpoint.",
    )
    metrics_bind_address: IPvAnyAddress = Field(
        default="127.0.0.1",  # type: ignore[arg-type]
        description="Bind address for metrics. Use 0.0.0.0 to allow scraping across containers.",
    )

    overseerr_url: HttpUrl | None = Field(
        default=None,
        description="Base URL of your Overseerr instance (e.g. http://overseerr:5055)",
    )
    overseerr_api_key: SecretStr | None = Field(
        default=None,
        description="Overseerr API key (from Overseerr admin UI: Settings → General → API Key)",
    )
    overseerr_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="HTTP timeout for Overseerr API calls in seconds",
    )

    sonarr_url: HttpUrl | None = Field(
        default=None,
        description="Base URL of your Sonarr instance (e.g. http://sonarr:8989)",
    )
    sonarr_api_key: SecretStr | None = Field(
        default=None,
        description="Sonarr API key (from Sonarr admin UI: Settings → General → API Key)",
    )
    sonarr_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="HTTP timeout for Sonarr API calls in seconds",
    )

    radarr_url: HttpUrl | None = Field(
        default=None,
        description="Base URL of your Radarr instance (e.g. http://radarr:7878)",
    )
    radarr_api_key: SecretStr | None = Field(
        default=None,
        description="Radarr API key (from Radarr admin UI: Settings → General → API Key)",
    )
    radarr_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="HTTP timeout for Radarr API calls in seconds",
    )

    claude_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        description="Wall-clock timeout for a single Claude SDK request.",
    )
    telegram_max_message_chars: int = Field(
        default=2000,
        gt=0,
        le=4096,
        description="Reject Telegram messages longer than this many characters before forwarding to Claude.",  # noqa: E501
    )

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


def clear_sdk_credentials() -> None:
    """Remove SDK auth tokens from os.environ on shutdown.

    Limits the window in which live credentials sit in the process environment
    after the bot stops accepting work.
    """
    os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    os.environ.pop("ANTHROPIC_API_KEY", None)
