from decimal import Decimal
from uuid import UUID

import pytest

from app.config import Settings
from app.db import get_session
from app.main import app
from app.models import AIInvocationStatus, Workspace
from app.services.ai_invocation_usage import AIInvocationUsageService
from app.services.ai_model_tiers import AIModelTier, AIModelTierResolver
from app.services.workspace_ai_usage_limits import (
    AIWorkspaceUsageLimitConfigurationError,
    AIWorkspaceUsageLimitOutcome,
    AIWorkspaceUsageLimitPolicy,
    AIWorkspaceUsageLimitReasonCode,
    AIWorkspaceUsageLimitRequest,
    AIWorkspaceUsageLimitRequestError,
)


def _workspace(client, slug: str) -> dict:
    response = client.post("/api/workspaces", json={"slug": slug, "name": slug.title()})
    assert response.status_code == 201
    return response.json()


def _settings() -> Settings:
    return Settings(
        ai_model_tier_mappings={
            "economy": {"provider": "provider_a", "model": "economy-model"},
            "standard": {"provider": "provider_b", "model": "standard-model"},
            "premium": {"provider": "provider_c", "model": "premium-model"},
        }
    )


def _policy(session):
    return AIWorkspaceUsageLimitPolicy(
        AIInvocationUsageService(session),
        AIModelTierResolver.from_settings(_settings()),
    )


def _stored_workspace(client, workspace: dict):
    session_dependency = app.dependency_overrides[get_session]
    session = next(session_dependency())
    return session, session.get(Workspace, UUID(workspace["id"]))


def _record_usage(session, workspace: Workspace, *, tokens: int = 10, cost: Decimal | None = Decimal("1")):
    AIInvocationUsageService(session).record(
        workspace,
        task_identifier="sales.reply",
        agent_identifier="sales",
        provider="provider_a",
        model="model-a",
        input_tokens=tokens,
        output_tokens=0,
        latency_ms=1,
        estimated_cost=cost,
        status=AIInvocationStatus.SUCCESSFUL,
    )


def test_no_configured_limits_allows_and_deterministic_tier_does_not_consume_budget(client):
    workspace = _workspace(client, "ai-limit-none")
    session, stored = _stored_workspace(client, workspace)
    try:
        policy = _policy(session)
        decision = policy.evaluate(
            AIWorkspaceUsageLimitRequest(stored, AIModelTier.STANDARD)
        )
        deterministic = policy.evaluate(
            AIWorkspaceUsageLimitRequest(stored, AIModelTier.NONE)
        )
    finally:
        session.close()

    assert decision.outcome is AIWorkspaceUsageLimitOutcome.ALLOWED
    assert decision.tier is AIModelTier.STANDARD
    assert decision.reason_code is AIWorkspaceUsageLimitReasonCode.WITHIN_LIMITS
    assert deterministic.outcome is AIWorkspaceUsageLimitOutcome.ALLOWED
    assert deterministic.tier is AIModelTier.NONE


def test_usage_under_all_configured_limits_is_allowed_with_decimal_comparison(client):
    workspace = _workspace(client, "ai-limit-under")
    session, stored = _stored_workspace(client, workspace)
    try:
        stored.ai_invocation_limit = 2
        stored.ai_total_token_limit = 20
        stored.ai_estimated_spend_limit = Decimal("1.50")
        session.add(stored)
        session.commit()
        _record_usage(session, stored, tokens=10, cost=Decimal("1.00"))

        decision = _policy(session).evaluate(
            AIWorkspaceUsageLimitRequest(
                stored,
                AIModelTier.STANDARD,
                expected_total_tokens=10,
                expected_estimated_cost=Decimal("0.50"),
                pricing_known=True,
            )
        )
    finally:
        session.close()

    assert decision.outcome is AIWorkspaceUsageLimitOutcome.ALLOWED
    assert decision.reason_code is AIWorkspaceUsageLimitReasonCode.WITHIN_LIMITS


@pytest.mark.parametrize(
    ("field", "value", "request_kwargs", "reason_code"),
    [
        ("ai_invocation_limit", 1, {}, AIWorkspaceUsageLimitReasonCode.INVOCATION_LIMIT_REACHED),
        (
            "ai_total_token_limit",
            10,
            {"expected_total_tokens": 1},
            AIWorkspaceUsageLimitReasonCode.TOKEN_LIMIT_REACHED,
        ),
        (
            "ai_estimated_spend_limit",
            Decimal("1.00"),
            {"expected_estimated_cost": Decimal("0.01"), "pricing_known": True},
            AIWorkspaceUsageLimitReasonCode.SPEND_LIMIT_REACHED,
        ),
    ],
)
def test_reached_hard_limits_block_before_any_llm_call(client, monkeypatch, field, value, request_kwargs, reason_code):
    monkeypatch.setattr(
        "app.services.llm.build_llm",
        lambda settings: (_ for _ in ()).throw(AssertionError("limits must not build an LLM")),
    )
    workspace = _workspace(client, f"ai-limit-{field}")
    session, stored = _stored_workspace(client, workspace)
    try:
        setattr(stored, field, value)
        session.add(stored)
        session.commit()
        _record_usage(session, stored)
        decision = _policy(session).evaluate(
            AIWorkspaceUsageLimitRequest(stored, AIModelTier.STANDARD, **request_kwargs)
        )
    finally:
        session.close()

    assert decision.outcome is AIWorkspaceUsageLimitOutcome.BLOCKED
    assert decision.tier is None
    assert decision.reason_code is reason_code
    assert decision.explanation


def test_workspace_usage_isolation_is_preserved(client):
    workspace_a = _workspace(client, "ai-limit-isolation-a")
    workspace_b = _workspace(client, "ai-limit-isolation-b")
    session, a = _stored_workspace(client, workspace_a)
    try:
        b = session.get(Workspace, UUID(workspace_b["id"]))
        a.ai_invocation_limit = 1
        b.ai_invocation_limit = 1
        session.add(a)
        session.add(b)
        session.commit()
        _record_usage(session, a)
        policy = _policy(session)
        a_decision = policy.evaluate(AIWorkspaceUsageLimitRequest(a, AIModelTier.STANDARD))
        b_decision = policy.evaluate(AIWorkspaceUsageLimitRequest(b, AIModelTier.STANDARD))
    finally:
        session.close()

    assert a_decision.outcome is AIWorkspaceUsageLimitOutcome.BLOCKED
    assert b_decision.outcome is AIWorkspaceUsageLimitOutcome.ALLOWED


def test_configured_premium_downgrade_is_deterministic_and_never_upgrades(client):
    workspace = _workspace(client, "ai-limit-downgrade")
    session, stored = _stored_workspace(client, workspace)
    try:
        stored.ai_permitted_model_tiers = ["economy", "standard"]
        stored.ai_model_tier_downgrade_mappings = {"premium": "standard"}
        session.add(stored)
        session.commit()
        decision = _policy(session).evaluate(
            AIWorkspaceUsageLimitRequest(stored, AIModelTier.PREMIUM)
        )
    finally:
        session.close()

    assert decision.outcome is AIWorkspaceUsageLimitOutcome.DOWNGRADED
    assert decision.tier is AIModelTier.STANDARD
    assert decision.reason_code is AIWorkspaceUsageLimitReasonCode.DOWNGRADED_FOR_WORKSPACE_POLICY


def test_invalid_upgrade_downgrade_configuration_is_rejected(client):
    workspace = _workspace(client, "ai-limit-upgrade")
    session, stored = _stored_workspace(client, workspace)
    try:
        stored.ai_model_tier_downgrade_mappings = {"economy": "standard"}
        session.add(stored)
        session.commit()
        with pytest.raises(AIWorkspaceUsageLimitConfigurationError, match="lower LLM tier"):
            _policy(session).evaluate(AIWorkspaceUsageLimitRequest(stored, AIModelTier.ECONOMY))
    finally:
        session.close()


def test_hard_limit_blocks_instead_of_downgrading(client):
    workspace = _workspace(client, "ai-limit-hard-before-downgrade")
    session, stored = _stored_workspace(client, workspace)
    try:
        stored.ai_invocation_limit = 0
        stored.ai_permitted_model_tiers = ["economy", "standard"]
        stored.ai_model_tier_downgrade_mappings = {"premium": "standard"}
        session.add(stored)
        session.commit()
        decision = _policy(session).evaluate(
            AIWorkspaceUsageLimitRequest(stored, AIModelTier.PREMIUM)
        )
    finally:
        session.close()

    assert decision.outcome is AIWorkspaceUsageLimitOutcome.BLOCKED
    assert decision.reason_code is AIWorkspaceUsageLimitReasonCode.INVOCATION_LIMIT_REACHED


def test_unavailable_downgrade_target_blocks_deterministically(client):
    workspace = _workspace(client, "ai-limit-target-unavailable")
    session, stored = _stored_workspace(client, workspace)
    try:
        stored.ai_permitted_model_tiers = ["standard"]
        stored.ai_model_tier_downgrade_mappings = {"premium": "standard"}
        session.add(stored)
        session.commit()
        policy = AIWorkspaceUsageLimitPolicy(
            AIInvocationUsageService(session),
            AIModelTierResolver.from_settings(
                Settings(ai_model_tier_mappings={"economy": {"provider": "a", "model": "b"}})
            ),
        )
        decision = policy.evaluate(AIWorkspaceUsageLimitRequest(stored, AIModelTier.PREMIUM))
    finally:
        session.close()

    assert decision.outcome is AIWorkspaceUsageLimitOutcome.BLOCKED
    assert decision.reason_code is AIWorkspaceUsageLimitReasonCode.DOWNGRADE_TARGET_UNAVAILABLE


def test_unknown_pricing_is_conservatively_blocked_when_spend_limit_is_enforced(client):
    workspace = _workspace(client, "ai-limit-unknown-price")
    session, stored = _stored_workspace(client, workspace)
    try:
        stored.ai_estimated_spend_limit = Decimal("5.00")
        session.add(stored)
        session.commit()
        decision = _policy(session).evaluate(
            AIWorkspaceUsageLimitRequest(stored, AIModelTier.STANDARD, pricing_known=False)
        )
    finally:
        session.close()

    assert decision.outcome is AIWorkspaceUsageLimitOutcome.BLOCKED
    assert decision.reason_code is AIWorkspaceUsageLimitReasonCode.UNKNOWN_PRICING_WITH_SPEND_LIMIT


def test_prior_unknown_pricing_is_not_treated_as_zero_under_a_spend_limit(client):
    workspace = _workspace(client, "ai-limit-prior-unknown-price")
    session, stored = _stored_workspace(client, workspace)
    try:
        stored.ai_estimated_spend_limit = Decimal("5.00")
        session.add(stored)
        session.commit()
        _record_usage(session, stored, cost=None)
        decision = _policy(session).evaluate(
            AIWorkspaceUsageLimitRequest(
                stored,
                AIModelTier.STANDARD,
                expected_estimated_cost=Decimal("0.01"),
                pricing_known=True,
            )
        )
    finally:
        session.close()

    assert decision.outcome is AIWorkspaceUsageLimitOutcome.BLOCKED
    assert decision.reason_code is AIWorkspaceUsageLimitReasonCode.UNKNOWN_PRICING_WITH_SPEND_LIMIT


def test_token_estimate_and_decimal_safe_request_values_are_validated(client):
    workspace = _workspace(client, "ai-limit-request-validation")
    session, stored = _stored_workspace(client, workspace)
    try:
        stored.ai_total_token_limit = 100
        session.add(stored)
        session.commit()
        policy = _policy(session)
        missing_estimate = policy.evaluate(AIWorkspaceUsageLimitRequest(stored, AIModelTier.STANDARD))
        with pytest.raises(AIWorkspaceUsageLimitRequestError, match="binary float"):
            policy.evaluate(
                AIWorkspaceUsageLimitRequest(
                    stored,
                    AIModelTier.STANDARD,
                    expected_estimated_cost=0.1,
                    pricing_known=True,
                )
            )
    finally:
        session.close()

    assert missing_estimate.outcome is AIWorkspaceUsageLimitOutcome.BLOCKED
    assert missing_estimate.reason_code is AIWorkspaceUsageLimitReasonCode.TOKEN_ESTIMATE_REQUIRED
