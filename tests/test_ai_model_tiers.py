import pytest
from pydantic import ValidationError

from app.config import Settings
from app.services.ai_model_tiers import (
    AIModelTier,
    AIModelTierResolutionError,
    AIModelTierResolver,
)
from app.services.llm import OpenAICompatibleLLM, build_llm


def _settings_with_tiers() -> Settings:
    return Settings(
        ai_model_tier_mappings={
            "economy": {"provider": "provider_a", "model": "economy-model"},
            "standard": {"provider": "provider_b", "model": "standard-model"},
            "premium": {"provider": "provider_c", "model": "premium-model"},
        }
    )


@pytest.mark.parametrize("value", ["none", "economy", "standard", "premium"])
def test_all_supported_tiers_validate(value):
    assert AIModelTier(value).value == value


def test_none_resolves_without_provider_or_model():
    selection = AIModelTierResolver.from_settings(Settings()).resolve(AIModelTier.NONE)

    assert selection.tier is AIModelTier.NONE
    assert selection.provider is None
    assert selection.model is None


@pytest.mark.parametrize(
    ("tier", "provider", "model"),
    [
        (AIModelTier.ECONOMY, "provider_a", "economy-model"),
        (AIModelTier.STANDARD, "provider_b", "standard-model"),
        (AIModelTier.PREMIUM, "provider_c", "premium-model"),
    ],
)
def test_configured_llm_tiers_resolve_to_provider_neutral_selections(tier, provider, model):
    selection = AIModelTierResolver.from_settings(_settings_with_tiers()).resolve(tier)

    assert selection.tier is tier
    assert selection.provider == provider
    assert selection.model == model


def test_premium_is_never_an_implicit_fallback():
    resolver = AIModelTierResolver.from_settings(
        Settings(
            ai_model_tier_mappings={
                "economy": {"provider": "provider_a", "model": "economy-model"},
                "standard": {"provider": "provider_b", "model": "standard-model"},
            }
        )
    )

    with pytest.raises(AIModelTierResolutionError, match="premium"):
        resolver.resolve(AIModelTier.PREMIUM)
    assert resolver.resolve(AIModelTier.STANDARD).model == "standard-model"


@pytest.mark.parametrize(
    "mappings",
    [
        {"economy": {"provider": "", "model": "economy-model"}},
        {"economy": {"provider": "provider_a", "model": ""}},
        {"none": {"provider": "provider_a", "model": "model-a"}},
        {"unsupported": {"provider": "provider_a", "model": "model-a"}},
    ],
)
def test_malformed_or_unsupported_tier_mappings_are_rejected(mappings):
    with pytest.raises(ValidationError):
        Settings(ai_model_tier_mappings=mappings)


def test_unknown_tier_is_rejected_without_creating_an_llm_client(monkeypatch):
    resolver = AIModelTierResolver.from_settings(_settings_with_tiers())
    monkeypatch.setattr(
        "app.services.llm.build_llm",
        lambda settings: (_ for _ in ()).throw(AssertionError("resolution must not build an LLM")),
    )

    with pytest.raises(AIModelTierResolutionError, match="Unknown"):
        resolver.resolve("unknown")


def test_existing_llm_transport_configuration_remains_compatible():
    settings = Settings(
        llm_mode="openai_compatible",
        llm_api_key="test-key",
        llm_model="existing-model",
    )

    client = build_llm(settings)

    assert isinstance(client, OpenAICompatibleLLM)
    assert client.settings.llm_model == "existing-model"
