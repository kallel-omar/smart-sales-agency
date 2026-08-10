"""Offline, provider-neutral seams for Sales conversation quality evaluation."""

from __future__ import annotations

from dataclasses import dataclass

from app.departments.sales.language_policy import (
    SalesCommunicationStyle,
    validate_sales_script_consistency,
)


@dataclass(frozen=True, slots=True)
class ConversationQualityEvaluation:
    repeated_question: bool
    excessive_question_load: bool
    script_consistent: bool
    empty_response: bool


def evaluate_conversation_quality(
    reply: str,
    prior_sales_messages: tuple[str, ...],
    *,
    expected_style: SalesCommunicationStyle,
) -> ConversationQualityEvaluation:
    """Evaluate objective reply signals without a model call or response mutation.

    The evaluator is intentionally a quality-assurance seam, not a second Sales
    policy engine: it flags an exact repeated Sales question, excessive question
    load, obvious Tunisian script mismatch, and an empty response.
    """

    normalized = reply.strip().lower()
    question = normalized.rstrip("?").strip()
    return ConversationQualityEvaluation(
        repeated_question=bool(
            question
            and reply.count("?")
            and any(
                question == message.strip().lower().rstrip("?").strip()
                for message in prior_sales_messages
            )
        ),
        excessive_question_load=reply.count("?") > 1,
        script_consistent=validate_sales_script_consistency(
            text=reply,
            style=expected_style,
        ).is_consistent,
        empty_response=not bool(normalized),
    )
