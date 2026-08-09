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
}


def runtime_readiness_reason_message(code: IntegrationRuntimeReadinessReasonCode) -> str:
    """Return a deterministic explanation that never includes secret material."""
    return _SAFE_MESSAGES[code]
