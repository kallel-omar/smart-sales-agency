from dataclasses import replace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.departments.sales.evidence import (
    SalesEvidenceClassification,
    SalesEvidenceItem,
    SalesEvidenceSourceType,
)
from app.departments.sales.playbook import SalesPlaybookV1
from app.departments.sales.qualification_facts import (
    produce_lead_research_qualification_evidence,
)
from app.departments.sales.research_qualification_expertise import (
    AccountResearchInput,
    AuthoritativeSalesEvidence,
    detect_buying_signals,
    safe_account_research_output,
)


def policy(criterion_type: str = "business_problem") -> SalesPlaybookV1:
    return SalesPlaybookV1.model_validate(
        {
            "schema_version": 1,
            "icp": {
                "criteria": [
                    {
                        "key": f"target_{criterion_type}",
                        "criterion_type": criterion_type,
                        "operator": "equals",
                        "values": ["we struggle with slow customer response"],
                        "importance": "required",
                    }
                ],
                "disqualifiers": [],
            },
            "qualification": {"required_information": []},
        }
    )


def source(*claims: str) -> AccountResearchInput:
    evidence = [
        AuthoritativeSalesEvidence(
            "lead.company_name",
            SalesEvidenceSourceType.LEAD_RECORD,
            "Acme",
        )
    ]
    evidence.extend(
        AuthoritativeSalesEvidence(
            f"conversation.message-{index}",
            SalesEvidenceSourceType.CONVERSATION,
            claim,
            "2026-08-27T10:00:00",
        )
        for index, claim in enumerate(claims, start=1)
    )
    return AccountResearchInput(uuid4(), uuid4(), tuple(evidence))


def produced(source_value: AccountResearchInput, *, playbook=None):
    account = safe_account_research_output(source_value)
    buying = detect_buying_signals(source_value)
    return produce_lead_research_qualification_evidence(
        playbook or policy(),
        account,
        buying,
        source_value,
    )


def test_explicit_business_problem_is_confirmed_from_deterministic_signal() -> None:
    evidence = produced(source("We struggle with slow customer response"))

    confirmed = [item for item in evidence if item["classification"] == "confirmed"]
    assert confirmed == [
        {
            "type": "qualification_fact",
            "schema_version": 1,
            "key": "business_problem",
            "criterion_type": "business_problem",
            "classification": "confirmed",
            "value": "we struggle with slow customer response",
        }
    ]


def test_interpreted_potential_need_remains_inference() -> None:
    evidence = produced(source("We may need a faster customer response process"))

    assert evidence
    assert {item["classification"] for item in evidence} == {"inference"}


def test_model_proposed_confirmed_guess_cannot_self_authorize() -> None:
    source_value = source("Hello")
    account = replace(
        safe_account_research_output(source_value),
        confirmed_facts=(
            SalesEvidenceItem(
                SalesEvidenceClassification.CONFIRMED,
                "Invented business problem",
                SalesEvidenceSourceType.CONVERSATION,
                "conversation.message-1",
                "2026-08-27T10:00:00",
            ),
        ),
    )

    evidence = produce_lead_research_qualification_evidence(
        policy(),
        account,
        detect_buying_signals(source_value),
        source_value,
    )

    assert all(item["classification"] != "confirmed" for item in evidence)


def test_missing_evidence_produces_no_fabricated_fact() -> None:
    assert produced(source()) == []


def test_unsupported_playbook_criterion_produces_no_fact() -> None:
    assert produced(
        source("We struggle with slow customer response"),
        playbook=policy("country"),
    ) == []


def test_invalid_playbook_criterion_type_is_rejected_before_production() -> None:
    with pytest.raises(ValidationError):
        SalesPlaybookV1.model_validate(
            {
                "schema_version": 1,
                "icp": {
                    "criteria": [
                        {
                            "key": "unsupported",
                            "criterion_type": "model_guess",
                            "operator": "equals",
                            "values": ["value"],
                            "importance": "required",
                        }
                    ],
                    "disqualifiers": [],
                },
                "qualification": {"required_information": []},
            }
        )


def test_invalid_or_unbounded_candidate_is_dropped() -> None:
    source_value = source("Hello")
    account = replace(
        safe_account_research_output(source_value),
        potential_needs=(
            SalesEvidenceItem(
                SalesEvidenceClassification.INFERENCE,
                "x" * 201,
                SalesEvidenceSourceType.CONVERSATION,
                "conversation.message-1",
                "2026-08-27T10:00:00",
            ),
        ),
    )

    assert produce_lead_research_qualification_evidence(
        policy(),
        account,
        detect_buying_signals(source_value),
        source_value,
    ) == []


def test_only_playbook_relevant_type_is_produced() -> None:
    source_value = source("We struggle with slow customer response")

    assert produced(source_value, playbook=policy("industry")) == []
    assert produced(source_value, playbook=policy())


def test_conflicting_confirmed_business_problems_are_preserved() -> None:
    evidence = produced(
        source(
            "We struggle with slow customer response",
            "Our problem is inaccurate customer routing",
        )
    )

    confirmed_values = {
        item["value"]
        for item in evidence
        if item["classification"] == "confirmed"
    }
    assert confirmed_values == {
        "we struggle with slow customer response",
        "our problem is inaccurate customer routing",
    }


def test_envelope_is_exact_versioned_and_contains_no_raw_reference() -> None:
    evidence = produced(source("We struggle with slow customer response"))

    assert set(evidence[0]) == {
        "type",
        "schema_version",
        "key",
        "criterion_type",
        "classification",
        "value",
    }
    assert "conversation.message" not in str(evidence[0])
