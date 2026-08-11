"""Stable, safe reasons for integration runtime configuration readiness."""

from enum import StrEnum


class IntegrationRuntimeReadinessReasonCode(StrEnum):
    """Provider-neutral blockers that are not already outbound readiness codes."""

    WORKSPACE_INACTIVE = "workspace_inactive"
    INBOUND_VERIFIER_NOT_CONFIGURED = "inbound_verifier_not_configured"
    SECRET_REFERENCE_MISSING = "secret_reference_missing"
    SECRET_REFERENCE_INVALID = "secret_reference_invalid"
    SECRET_UNRESOLVABLE = "secret_unresolvable"
    OUTBOUND_ADAPTER_NOT_REGISTERED = "outbound_adapter_not_registered"
    OUTBOUND_ADAPTER_CAPABILITY_MISMATCH = "outbound_adapter_capability_mismatch"
    OUTBOUND_CONFIGURATION_MISSING = "outbound_configuration_missing"
    OUTBOUND_CONFIGURATION_INVALID = "outbound_configuration_invalid"
    EXTERNAL_ACCOUNT_ID_MISSING = "external_account_id_missing"
    OUTBOUND_WEBHOOK_SIGNING_DISABLED = "outbound_webhook_signing_disabled"
    OUTBOUND_APPROVAL_GATE_DISABLED = "outbound_approval_gate_disabled"
    PROVIDER_CAPABILITY_NOT_SUPPORTED = "provider_capability_not_supported"


_SAFE_MESSAGES: dict[IntegrationRuntimeReadinessReasonCode, str] = {
    IntegrationRuntimeReadinessReasonCode.WORKSPACE_INACTIVE: (
        "The workspace is inactive."
    ),
    IntegrationRuntimeReadinessReasonCode.INBOUND_VERIFIER_NOT_CONFIGURED: (
        "No supported inbound webhook verifier is configured for this provider."
    ),
    IntegrationRuntimeReadinessReasonCode.SECRET_REFERENCE_MISSING: (
        "The required integration secret reference is missing."
    ),
    IntegrationRuntimeReadinessReasonCode.SECRET_REFERENCE_INVALID: (
        "The integration secret reference does not satisfy the configured policy."
    ),
    IntegrationRuntimeReadinessReasonCode.SECRET_UNRESOLVABLE: (
        "The required integration secret cannot be resolved by the configured backend."
    ),
    IntegrationRuntimeReadinessReasonCode.OUTBOUND_ADAPTER_NOT_REGISTERED: (
        "No outbound delivery adapter is registered for this provider."
    ),
    IntegrationRuntimeReadinessReasonCode.OUTBOUND_ADAPTER_CAPABILITY_MISMATCH: (
        "The outbound delivery adapter does not support the MVP message action."
    ),
    IntegrationRuntimeReadinessReasonCode.OUTBOUND_CONFIGURATION_MISSING: (
        "The required outbound transport configuration is missing."
    ),
    IntegrationRuntimeReadinessReasonCode.OUTBOUND_CONFIGURATION_INVALID: (
        "The outbound transport configuration is invalid."
    ),
    IntegrationRuntimeReadinessReasonCode.EXTERNAL_ACCOUNT_ID_MISSING: (
        "The provider account identifier required by this channel is missing."
    ),
    IntegrationRuntimeReadinessReasonCode.OUTBOUND_WEBHOOK_SIGNING_DISABLED: (
        "The outbound webhook transport must use signed requests."
    ),
    IntegrationRuntimeReadinessReasonCode.OUTBOUND_APPROVAL_GATE_DISABLED: (
        "The outbound approval gate is not enabled for this runtime."
    ),
    IntegrationRuntimeReadinessReasonCode.PROVIDER_CAPABILITY_NOT_SUPPORTED: (
        "This provider does not support the requested channel capability."
    ),
}


def runtime_readiness_reason_message(code: IntegrationRuntimeReadinessReasonCode) -> str:
    """Return a deterministic explanation that never includes secret material."""
    return _SAFE_MESSAGES[code]
