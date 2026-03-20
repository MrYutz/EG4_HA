"""Project settings loaded from environment variables and .env files."""

from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class EG4PortalSettings(BaseModel):
    """Credentials for the EG4 cloud portal."""

    username: str = ""
    password: SecretStr = Field(default_factory=lambda: SecretStr(""))


class HomeAssistantSettings(BaseModel):
    """Optional Home Assistant API settings used by tooling."""

    base_url: str = "http://192.168.0.253:8123"
    token: SecretStr = Field(default_factory=lambda: SecretStr(""))


class AppSettings(BaseSettings):
    """Top-level application settings."""

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    eg4_portal: EG4PortalSettings = Field(default_factory=EG4PortalSettings)
    home_assistant: HomeAssistantSettings = Field(default_factory=HomeAssistantSettings)

    @property
    def eg4_username(self) -> str:
        """Backward-compatible access for existing tests/scripts."""
        return self.eg4_portal.username

    @property
    def eg4_password(self) -> str:
        """Backward-compatible access for existing tests/scripts."""
        return self.eg4_portal.password.get_secret_value()

    @property
    def has_eg4_credentials(self) -> bool:
        """Return True if EG4 portal credentials are present."""
        return bool(self.eg4_username and self.eg4_password)


settings = AppSettings()
