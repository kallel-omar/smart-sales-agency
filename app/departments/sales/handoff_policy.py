"""Deterministic, provider-neutral human-handoff policy for Sales."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.models import SalesHandoffReasonCode

_SEPARATOR = r"[\W_]+"
_EXPLICIT_HUMAN_REQUEST_PATTERNS = (
    re.compile(rf"\bhuman{_SEPARATOR}agent\b", re.IGNORECASE),
    re.compile(
        rf"\b(?:talk|speak){_SEPARATOR}(?:to|with){_SEPARATOR}"
        rf"(?:a{_SEPARATOR})?(?:human|person)\b",
        re.IGNORECASE,
    ),
    re.compile(rf"\breal{_SEPARATOR}person\b", re.IGNORECASE),
    re.compile(rf"\bcustomer{_SEPARATOR}support\b", re.IGNORECASE),
    re.compile(rf"\bhuman{_SEPARATOR}support\b", re.IGNORECASE),
    re.compile(r"\brepresentative\b", re.IGNORECASE),
    re.compile(rf"\blive{_SEPARATOR}agent\b", re.IGNORECASE),
)


class SalesCommercialEscalationType(StrEnum):
    """Trusted commercial exception signals supplied by domain code."""

    UNSUPPORTED_DISCOUNT = "unsupported_discount"
    CUSTOM_PRICING = "custom_pricing"
    UNSUPPORTED_COMMITMENT = "unsupported_commitment"


@dataclass(frozen=True, slots=True)
class SalesHandoffSignals:
    """Typed, trusted facts used to decide whether a human must take over.

    These inputs are trusted application facts. An inbound API payload cannot
    set them directly; server-side deterministic domain logic may derive them
    after validating a relevant business condition.
    """

    human_requested: bool = False
    commercial_escalation: SalesCommercialEscalationType | None = None
    authoritative_information_unavailable: bool = False
    existing_approval_required: bool = False


def derive_customer_handoff_signals(customer_message: str) -> SalesHandoffSignals:
    """Derive trusted handoff signals from explicit customer assistance requests."""

    if any(pattern.search(customer_message) for pattern in _EXPLICIT_HUMAN_REQUEST_PATTERNS):
        return SalesHandoffSignals(human_requested=True)
    return SalesHandoffSignals()


def merge_sales_handoff_signals(
    trusted_signals: SalesHandoffSignals | None,
    derived_signals: SalesHandoffSignals,
) -> SalesHandoffSignals:
    """Combine server-trusted handoff facts without dropping existing signals."""

    base = trusted_signals or SalesHandoffSignals()
    return SalesHandoffSignals(
        human_requested=base.human_requested or derived_signals.human_requested,
        commercial_escalation=base.commercial_escalation,
        authoritative_information_unavailable=(
            base.authoritative_information_unavailable
            or derived_signals.authoritative_information_unavailable
        ),
        existing_approval_required=(
            base.existing_approval_required or derived_signals.existing_approval_required
        ),
    )


@dataclass(frozen=True, slots=True)
class SalesHandoffDecision:
    """Safe domain result; this contains no prompt or provider information."""

    human_attention_required: bool
    reason_code: SalesHandoffReasonCode | None = None
    explanation: str | None = None


class SalesHandoffPolicy:
    """Evaluate stable handoff triggers without network, AI, or persistence work."""

    def decide(self, signals: SalesHandoffSignals) -> SalesHandoffDecision:
        if signals.human_requested:
            return self._required(
                SalesHandoffReasonCode.HUMAN_REQUESTED,
                "A team member needs to assist with this request.",
            )
        if signals.commercial_escalation is SalesCommercialEscalationType.UNSUPPORTED_DISCOUNT:
            return self._required(
                SalesHandoffReasonCode.UNSUPPORTED_DISCOUNT_REQUEST,
                "A team member needs to review the requested commercial terms.",
            )
        if signals.commercial_escalation is SalesCommercialEscalationType.CUSTOM_PRICING:
            return self._required(
                SalesHandoffReasonCode.CUSTOM_PRICING_REQUIRED,
                "A team member needs to review the requested commercial terms.",
            )
        if signals.commercial_escalation is SalesCommercialEscalationType.UNSUPPORTED_COMMITMENT:
            return self._required(
                SalesHandoffReasonCode.UNSUPPORTED_COMMERCIAL_COMMITMENT,
                "A team member needs to review the requested commercial terms.",
            )
        if signals.authoritative_information_unavailable:
            return self._required(
                SalesHandoffReasonCode.AUTHORITATIVE_INFORMATION_UNAVAILABLE,
                "A team member needs to confirm the requested information.",
            )
        if signals.existing_approval_required:
            return self._required(
                SalesHandoffReasonCode.APPROVAL_REQUIRED,
                "A required approval is pending review.",
            )
        return SalesHandoffDecision(human_attention_required=False)

    @staticmethod
    def _required(
        reason_code: SalesHandoffReasonCode,
        explanation: str,
    ) -> SalesHandoffDecision:
        return SalesHandoffDecision(
            human_attention_required=True,
            reason_code=reason_code,
            explanation=explanation,
        )


def render_sales_handoff_reply(decision: SalesHandoffDecision) -> str:
    """Return a short safe outcome without claiming an unsupported resolution."""

    if not decision.human_attention_required or not decision.explanation:
        raise ValueError("A required handoff decision is needed to render a handoff reply")
    return f"I can't confirm that request right now. {decision.explanation}"
