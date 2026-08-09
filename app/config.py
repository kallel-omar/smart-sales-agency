from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
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

    # Provider secrets are resolved from account-owned secret references by the
    # configured secret backend. The first backend reads environment variables.
    webhook_max_age_seconds: int = Field(default=300, gt=0, le=3_600)

    # Foundation for future scheduled audit cleanup. Read requests never delete
    # audit records; a future maintenance job must apply this policy explicitly.
    integration_account_audit_retention_days: int = Field(
        default=90,
        ge=1,
        le=3_650,
    )

    # Explicit outbound retries remain provider-neutral. Failure codes not in
    # this list are retryable by default until the maximum is reached.
    outbound_delivery_max_attempts: int = Field(default=3, ge=1, le=100)
    outbound_delivery_non_retryable_failure_codes: str = ""

    @field_validator("outbound_delivery_non_retryable_failure_codes")
    @classmethod
    def validate_outbound_delivery_non_retryable_failure_codes(cls, value: str) -> str:
        codes = [code.strip().lower() for code in value.split(",") if code.strip()]
        for code in codes:
            if not code.replace("_", "").isalnum() or not code[0].isalpha() or len(code) > 100:
                raise ValueError(
                    "Outbound delivery non-retryable failure codes must use "
                    "lowercase letters, numbers, or underscores"
                )
        if len(codes) != len(set(codes)):
            raise ValueError("Outbound delivery non-retryable failure codes must be unique")
        return ",".join(codes)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
