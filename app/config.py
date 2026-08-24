from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.services.ai_model_pricing import AIModelPricing
from app.services.ai_model_tiers import AIModelTier, AIModelTierMapping


class Settings(BaseSettings):
    app_name: str = "Smart Sales Agency"
    environment: Literal["development", "test", "production"] = Field(
        default="development",
        validation_alias=AliasChoices("APP_ENV", "ENVIRONMENT"),
    )
    app_host: str = Field(default="127.0.0.1", min_length=1, max_length=255)
    app_port: int = Field(
        default=8000,
        ge=1,
        le=65_535,
        validation_alias=AliasChoices("APP_PORT", "PORT"),
    )
    database_url: str = "sqlite:///./sales_agency.db"
    database_startup_max_attempts: int = Field(default=3, ge=1, le=20)
    database_startup_retry_delay_seconds: float = Field(default=1.0, ge=0, le=30)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "text"] = "json"
    metrics_enabled: bool = True
    api_docs_enabled: bool | None = None
    cors_allowed_origins: str = ""
    cors_allow_credentials: bool = False
    trusted_proxy_hosts: str = ""
    rate_limit_enabled: bool = True
    rate_limit_auth_login_limit: int = Field(default=120, ge=1, le=100_000)
    rate_limit_auth_login_window_seconds: int = Field(default=60, ge=1, le=86_400)
    rate_limit_integration_ingest_limit: int = Field(default=600, ge=1, le=100_000)
    rate_limit_integration_ingest_window_seconds: int = Field(default=60, ge=1, le=86_400)
    rate_limit_outbound_delivery_limit: int = Field(default=120, ge=1, le=100_000)
    rate_limit_outbound_delivery_window_seconds: int = Field(default=60, ge=1, le=86_400)
    rate_limit_ai_conversation_limit: int = Field(default=120, ge=1, le=100_000)
    rate_limit_ai_conversation_window_seconds: int = Field(default=60, ge=1, le=86_400)
    rate_limit_in_memory_max_buckets: int = Field(default=10_000, ge=100, le=1_000_000)
    rate_limit_in_memory_cleanup_batch_size: int = Field(default=500, ge=1, le=10_000)

    # Authentication is opt-in until Task 280 introduces the first human
    # credential flow. An empty development value cannot issue or verify a
    # token; production settings fail closed during validation.
    auth_token_secret: SecretStr = SecretStr("")
    auth_token_algorithm: Literal["HS256"] = "HS256"
    auth_token_expiration_seconds: int = Field(default=1_800, ge=60, le=3_600)
    auth_token_issuer: str = "smart-sales-agency"

    llm_mode: Literal["demo", "openai_compatible"] = "demo"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4.1-mini"
    llm_timeout_seconds: float = Field(default=45, gt=0, le=180)

    # Domain-level model capability mappings. They are intentionally optional
    # until a caller asks for a tier; the existing LLM settings remain the
    # backward-compatible transport configuration for current agent flows.
    ai_model_tier_mappings: dict[AIModelTier, AIModelTierMapping] = Field(default_factory=dict)
    # Provider/model pricing entries use decimal currency units per one million
    # tokens. They are deliberately independent of abstract capability tiers.
    ai_model_pricing: list[AIModelPricing] = Field(default_factory=list)
    # Premium capability is opt-in policy. A request must also carry a valid,
    # explicit premium justification before the routing policy may select it.
    ai_model_routing_premium_enabled: bool = False

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
    outbound_delivery_non_retryable_failure_classes: str = ""
    outbound_delivery_retry_delay_strategy: Literal["fixed", "exponential"] = "fixed"
    outbound_delivery_retry_delay_seconds: int = Field(default=0, ge=0, le=86_400)
    outbound_delivery_retry_delay_max_seconds: int = Field(default=3_600, ge=0, le=86_400)
    outbound_webhook_url: str = ""
    outbound_webhook_connect_timeout_seconds: float = Field(default=5, gt=0, le=60)
    outbound_webhook_read_timeout_seconds: float = Field(default=15, gt=0, le=120)
    outbound_webhook_signing_enabled: bool = False
    whatsapp_cloud_graph_api_base_url: str = "https://graph.facebook.com"
    whatsapp_cloud_graph_api_version: str = "v23.0"
    whatsapp_cloud_connect_timeout_seconds: float = Field(
        default=5,
        gt=0,
        le=60,
    )
    whatsapp_cloud_read_timeout_seconds: float = Field(
        default=15,
        gt=0,
        le=120,
    )
    integration_health_window_days: int = Field(default=30, ge=1, le=90)

    @field_validator("app_host")
    @classmethod
    def validate_app_host(cls, value: str) -> str:
        normalized = value.strip()
        if (
            not normalized
            or any(character.isspace() or ord(character) < 32 for character in normalized)
            or "/" in normalized
        ):
            raise ValueError("APP_HOST must be a single host or bind address")
        return normalized

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

    @field_validator("outbound_delivery_non_retryable_failure_classes")
    @classmethod
    def validate_outbound_delivery_non_retryable_failure_classes(cls, value: str) -> str:
        allowed = {
            "temporary",
            "permanent",
            "authentication",
            "rate_limit",
            "validation",
            "unknown",
        }
        classes = [item.strip().lower() for item in value.split(",") if item.strip()]
        if any(item not in allowed for item in classes):
            raise ValueError("Outbound delivery failure classes must be provider-neutral values")
        if len(classes) != len(set(classes)):
            raise ValueError("Outbound delivery failure classes must be unique")
        return ",".join(classes)

    @model_validator(mode="after")
    def validate_ai_model_tier_mappings(self) -> "Settings":
        if AIModelTier.NONE in self.ai_model_tier_mappings:
            raise ValueError("The none AI model tier must not have a provider/model mapping")
        pricing_keys = [(entry.provider, entry.model) for entry in self.ai_model_pricing]
        if len(pricing_keys) != len(set(pricing_keys)):
            raise ValueError("AI model pricing entries must be unique by provider and model")
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
