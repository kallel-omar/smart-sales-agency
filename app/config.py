from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Smart Sales Agency"
    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./sales_agency.db"

    llm_mode: Literal["demo", "openai_compatible"] = "demo"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4.1-mini"
    llm_timeout_seconds: float = Field(default=45, gt=0, le=180)

    require_human_approval: bool = True
    default_channel: Literal["console", "whatsapp", "email"] = "console"

    # Provider webhook secrets are supplied through secure runtime configuration.
    # The map is keyed by the provider value of an IntegrationAccount.
    webhook_hmac_secrets: dict[str, str] = Field(default_factory=dict)
    webhook_max_age_seconds: int = Field(default=300, gt=0, le=3_600)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
