import pytest

from app.config import Settings
from app.services.ai_model_routing import (
    AIPremiumJustification,
    AIModelRoutingError,
    AIModelRoutingPolicy,
    AIModelRoutingReasonCode,
    AIModelRoutingRequest,
    AIModelRoutingTask,
)
from app.services.ai_model_tiers import (
    AIModelTier,
    AIModelTierResolutionError,
    AIModelTierResolver,
)


def _settings(*, premium_enabled: bool = False, mappings: dict | None = None) -> Settings:
    return Settings(
        ai_model_routing_premium_enabled=premium_enabled,
        ai_model_tier_mappings=mappings
        or {
            "economy": {"provider": "provider_a", "model": "economy-model"},
            "standard": {"provider": "provider_b", "model": "standard-model"},
            "premium": {"provider": "provider_c", "model": "premium-model"},
        },
    )


def _policy(*, premium_enabled: bool = False) -> AIModelRoutingPolicy:
    return AIModelRoutingPolicy.from_settings(_settings(premium_enabled=premium_enabled))


def test_deterministic_task_routes_to_none():
    decision = _policy().decide(AIModelRoutingRequest(task=AIModelRoutingTask.DETERMINISTIC))

    assert decision.tier is AIModelTier.NONE
    assert decision.reason_code is AIModelRoutingReasonCode.DETERMINISTIC_TASK


@pytest.mark.parametrize(
    "task",
    [
        AIModelRoutingTask.CLASSIFICATION,
        AIModelRoutingTask.EXTRACTION,
        AIModelRoutingTask.SIMPLE_SUMMARY,
        AIModelRoutingTask.LIGHTWEIGHT_QUALIFICATION,
    ],
)
def test_lightweight_tasks_route_to_economy(task):
    decision = _policy().decide(AIModelRoutingRequest(task=task, agent_identifier="qualifier"))

    assert decision.tier is AIModelTier.ECONOMY
    assert decision.reason_code is AIModelRoutingReasonCode.LIGHTWEIGHT_AI_TASK


def test_normal_sales_conversation_routes_to_standard():
    decision = _policy().decide(
        AIModelRoutingRequest(task=AIModelRoutingTask.SALES_CONVERSATION, agent_identifier="sales")
    )

    assert decision.tier is AIModelTier.STANDARD
    assert decision.reason_code is AIModelRoutingReasonCode.STANDARD_CONVERSATION


@pytest.mark.parametrize(
    ("task", "justification", "reason_code"),
    [
        (
            AIModelRoutingTask.COMPLEX_REASONING,
            AIPremiumJustification.COMPLEX_CASE,
            AIModelRoutingReasonCode.EXPLICIT_COMPLEX_CASE,
        ),
        (
            AIModelRoutingTask.HIGH_VALUE_CASE,
            AIPremiumJustification.HIGH_VALUE_CASE,
            AIModelRoutingReasonCode.EXPLICIT_HIGH_VALUE_CASE,
        ),
    ],
)
def test_explicitly_justified_premium_task_routes_to_premium(task, justification, reason_code):
    decision = _policy(premium_enabled=True).decide(
        AIModelRoutingRequest(task=task, premium_justification=justification)
    )

    assert decision.tier is AIModelTier.PREMIUM
    assert decision.reason_code is reason_code


def test_premium_is_not_selected_without_explicit_justification():
    decision = _policy(premium_enabled=True).decide(
        AIModelRoutingRequest(task=AIModelRoutingTask.COMPLEX_REASONING)
    )

    assert decision.tier is AIModelTier.STANDARD
    assert decision.reason_code is AIModelRoutingReasonCode.PREMIUM_NOT_JUSTIFIED


def test_premium_is_not_a_fallback_when_standard_mapping_is_missing():
    settings = _settings(
        premium_enabled=True,
        mappings={"premium": {"provider": "provider_c", "model": "premium-model"}},
    )
    decision = AIModelRoutingPolicy.from_settings(settings).decide(
        AIModelRoutingRequest(task=AIModelRoutingTask.SALES_CONVERSATION)
    )

    assert decision.tier is AIModelTier.STANDARD
    assert decision.reason_code is AIModelRoutingReasonCode.STANDARD_CONVERSATION
    with pytest.raises(AIModelTierResolutionError, match="standard"):
        AIModelRoutingPolicy.resolve(decision, AIModelTierResolver.from_settings(settings))


def test_resolves_provider_and_model_through_task_257_configuration():
    settings = _settings()
    decision = AIModelRoutingPolicy.from_settings(settings).decide(
        AIModelRoutingRequest(task=AIModelRoutingTask.CLASSIFICATION)
    )

    selection = AIModelRoutingPolicy.resolve(decision, AIModelTierResolver.from_settings(settings))
    assert selection.tier is AIModelTier.ECONOMY
    assert selection.provider == "provider_a"
    assert selection.model == "economy-model"


def test_invalid_task_or_justification_is_rejected_deterministically():
    policy = _policy(premium_enabled=True)

    with pytest.raises(AIModelRoutingError, match="Unknown AI model routing task"):
        policy.decide(AIModelRoutingRequest(task="unknown"))
    with pytest.raises(AIModelRoutingError, match="Unknown premium justification"):
        policy.decide(
            AIModelRoutingRequest(
                task=AIModelRoutingTask.COMPLEX_REASONING,
                premium_justification="unknown",
            )
        )


def test_routing_and_resolution_do_not_create_an_llm_or_perform_network_io(monkeypatch):
    monkeypatch.setattr(
        "app.services.llm.build_llm",
        lambda settings: (_ for _ in ()).throw(AssertionError("routing must not build an LLM")),
    )
    policy = _policy()
    settings = _settings()

    decision = policy.decide(AIModelRoutingRequest(task=AIModelRoutingTask.CLASSIFICATION))
    selection = policy.resolve(decision, AIModelTierResolver.from_settings(settings))

    assert selection.model == "economy-model"


def test_existing_llm_configuration_remains_backward_compatible():
    settings = Settings(
        llm_mode="openai_compatible",
        llm_api_key="test-key",
        llm_model="existing-model",
    )

    assert settings.llm_model == "existing-model"
    assert AIModelRoutingPolicy.from_settings(settings).decide(
        AIModelRoutingRequest(task=AIModelRoutingTask.DETERMINISTIC)
    ).tier is AIModelTier.NONE
