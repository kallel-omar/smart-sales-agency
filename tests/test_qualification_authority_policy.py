import pytest

from app.departments.sales.icp_scoring import ICPFitStatus, ICPReasonCode
from app.departments.sales.qualification_authority import (
    QUALIFICATION_AUTHORITY_POLICY_VERSION,
    QualificationAuthorityDecisionValue,
    QualificationAuthorityNextAction,
    QualificationAuthorityReasonCode,
    QualificationDecisionPolicy,
)
from app.models import LeadStatus


def assessment(
    fit_status: ICPFitStatus,
    *,
    reason_codes: list[ICPReasonCode],
    mismatched_criteria: list[dict[str, object]] | None = None,
    matched_disqualifiers: list[dict[str, object]] | None = None,
    unknown_criteria: list[dict[str, object]] | None = None,
    required_information_gaps: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "status": "assessed",
        "fit_status": fit_status.value,
        "reason_codes": [item.value for item in reason_codes],
        "mismatched_criteria": mismatched_criteria or [],
        "matched_disqualifiers": matched_disqualifiers or [],
        "unknown_criteria": unknown_criteria or [],
        "required_information_gaps": required_information_gaps or [],
    }


def confirmed_rule(reason_code: ICPReasonCode) -> dict[str, object]:
    return {
        "rule_key": "rule",
        "status": "matched",
        "reason_code": reason_code.value,
        "evidence_references": ["research:1"],
    }


@pytest.mark.parametrize(
    ("icp_assessment", "reason"),
    [
        (
            assessment(
                ICPFitStatus.DISQUALIFIED,
                reason_codes=[ICPReasonCode.CONFIRMED_DISQUALIFIER_MATCHED],
                matched_disqualifiers=[
                    confirmed_rule(ICPReasonCode.CONFIRMED_VALUE_MATCH)
                ],
            ),
            QualificationAuthorityReasonCode.CONFIRMED_DISQUALIFIER_MATCHED,
        ),
        (
            assessment(
                ICPFitStatus.NOT_FIT,
                reason_codes=[ICPReasonCode.REQUIRED_CRITERION_MISMATCH],
                mismatched_criteria=[
                    confirmed_rule(ICPReasonCode.CONFIRMED_VALUE_MISMATCH)
                ],
            ),
            QualificationAuthorityReasonCode.CONFIRMED_REQUIRED_CRITERION_MISMATCH,
        ),
    ],
)
def test_confirmed_negative_icp_evidence_can_override_legacy_qualification(
    icp_assessment: dict[str, object],
    reason: QualificationAuthorityReasonCode,
) -> None:
    decision = QualificationDecisionPolicy().decide(
        legacy_qualified=True,
        icp_assessment=icp_assessment,
        current_lead_status=LeadStatus.RESEARCHED,
    )

    assert decision.decision is QualificationAuthorityDecisionValue.UNQUALIFIED
    assert decision.reason_codes == (reason,)
    assert decision.legacy_outcome == "qualified"
    assert decision.icp_authoritative is True
    assert decision.resulting_lead_status is LeadStatus.UNQUALIFIED
    assert decision.qualified_for_downstream is False
    assert decision.next_action is QualificationAuthorityNextAction.STOP_QUALIFICATION


def test_fit_requires_legacy_qualification_and_complete_required_information() -> None:
    complete = assessment(
        ICPFitStatus.FIT,
        reason_codes=[ICPReasonCode.ALL_REQUIRED_CRITERIA_MATCHED],
    )
    qualified = QualificationDecisionPolicy().decide(
        legacy_qualified=True,
        icp_assessment=complete,
        current_lead_status=LeadStatus.RESEARCHED,
    )
    preserved_negative = QualificationDecisionPolicy().decide(
        legacy_qualified=False,
        icp_assessment=complete,
        current_lead_status=LeadStatus.RESEARCHED,
    )

    assert qualified.decision is QualificationAuthorityDecisionValue.QUALIFIED
    assert qualified.resulting_lead_status is LeadStatus.QUALIFIED
    assert preserved_negative.decision is QualificationAuthorityDecisionValue.PRESERVE_LEGACY
    assert preserved_negative.resulting_lead_status is LeadStatus.UNQUALIFIED


@pytest.mark.parametrize(
    ("icp_assessment", "reason"),
    [
        (
            assessment(
                ICPFitStatus.FIT,
                reason_codes=[ICPReasonCode.ALL_REQUIRED_CRITERIA_MATCHED],
                required_information_gaps=[
                    {
                        "key": "budget",
                        "status": "gap",
                        "reason_code": "required_information_missing",
                        "evidence_references": [],
                    }
                ],
            ),
            QualificationAuthorityReasonCode.REQUIRED_INFORMATION_MISSING,
        ),
        (
            assessment(
                ICPFitStatus.INSUFFICIENT_INFORMATION,
                reason_codes=[ICPReasonCode.REQUIRED_CRITERION_UNKNOWN],
            ),
            QualificationAuthorityReasonCode.ICP_EVIDENCE_INSUFFICIENT,
        ),
        (
            assessment(
                ICPFitStatus.INSUFFICIENT_INFORMATION,
                reason_codes=[ICPReasonCode.REQUIRED_CRITERION_UNKNOWN],
                unknown_criteria=[
                    {
                        "rule_key": "market",
                        "status": "unknown",
                        "reason_code": "conflicting_evidence",
                        "evidence_references": ["research:1", "research:2"],
                    }
                ],
            ),
            QualificationAuthorityReasonCode.CONFIRMED_EVIDENCE_CONFLICT,
        ),
    ],
)
def test_unresolved_icp_evidence_preserves_nonterminal_lead_state(
    icp_assessment: dict[str, object],
    reason: QualificationAuthorityReasonCode,
) -> None:
    decision = QualificationDecisionPolicy().decide(
        legacy_qualified=True,
        icp_assessment=icp_assessment,
        current_lead_status=LeadStatus.RESEARCHED,
    )

    assert decision.decision is QualificationAuthorityDecisionValue.NEEDS_MORE_INFORMATION
    assert decision.reason_codes == (reason,)
    assert decision.resulting_lead_status is LeadStatus.RESEARCHED
    assert decision.qualified_for_downstream is False
    assert decision.more_information_required is True
    assert (
        decision.next_action
        is QualificationAuthorityNextAction.COLLECT_MORE_INFORMATION
    )


@pytest.mark.parametrize(
    ("icp_assessment", "reason"),
    [
        (
            {"status": "unavailable", "reason_code": "playbook_not_configured"},
            QualificationAuthorityReasonCode.PLAYBOOK_NOT_CONFIGURED,
        ),
        (
            {"status": "unavailable", "reason_code": "playbook_invalid"},
            QualificationAuthorityReasonCode.PLAYBOOK_INVALID,
        ),
        (
            assessment(
                ICPFitStatus.NOT_FIT,
                reason_codes=[],
                mismatched_criteria=[
                    confirmed_rule(ICPReasonCode.CONFIRMED_VALUE_MISMATCH)
                ],
            ),
            QualificationAuthorityReasonCode.ICP_ASSESSMENT_INVALID,
        ),
    ],
)
def test_unavailable_or_invalid_icp_preserves_legacy_authority(
    icp_assessment: dict[str, object],
    reason: QualificationAuthorityReasonCode,
) -> None:
    decision = QualificationDecisionPolicy().decide(
        legacy_qualified=True,
        icp_assessment=icp_assessment,
        current_lead_status=LeadStatus.RESEARCHED,
    )

    assert decision.decision is QualificationAuthorityDecisionValue.PRESERVE_LEGACY
    assert decision.reason_codes == (reason,)
    assert decision.resulting_lead_status is LeadStatus.QUALIFIED
    assert decision.icp_authoritative is False
    assert decision.qualified_for_downstream is True
    assert decision.as_dict()["policy_version"] == QUALIFICATION_AUTHORITY_POLICY_VERSION
