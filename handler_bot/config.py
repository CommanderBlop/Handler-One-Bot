"""Centralized configuration loaded from environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Values come from environment variables (or .env)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    anthropic_api_key: str
    discord_bot_token: str

    claude_model: str = "claude-opus-4-7"
    claude_max_tokens: int = 8000
    claude_effort: str = "high"

    handler_history_fetch_limit: int = 30
    handler_query_timeout: int = 120

    allowed_user_ids: str = ""
    allowed_guild_ids: str = ""

    log_level: str = "INFO"

    @property
    def allowed_user_id_set(self) -> set[int]:
        return _parse_id_set(self.allowed_user_ids)

    @property
    def allowed_guild_id_set(self) -> set[int]:
        return _parse_id_set(self.allowed_guild_ids)


def _parse_id_set(raw: str) -> set[int]:
    if not raw.strip():
        return set()
    return {int(p) for p in raw.split(",") if p.strip()}
