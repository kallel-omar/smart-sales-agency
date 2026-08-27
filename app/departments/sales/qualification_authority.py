"""Deterministic authority policy for persisted Sales qualification decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.departments.sales.icp_scoring import ICPFitStatus, ICPReasonCode
from app.models import LeadStatus

QUALIFICATION_AUTHORITY_POLICY_VERSION = "sales.qualification_authority.v1"


class QualificationAuthorityDecisionValue(StrEnum):
    QUALIFIED = "qualified"
    UNQUALIFIED = "unqualified"
    NEEDS_MORE_INFORMATION = "needs_more_information"
    PRESERVE_LEGACY = "preserve_legacy"


class QualificationAuthorityReasonCode(StrEnum):
    CONFIRMED_DISQUALIFIER_MATCHED = "confirmed_disqualifier_matched"
    CONFIRMED_REQUIRED_CRITERION_MISMATCH = (
        "confirmed_required_criterion_mismatch"
    )
    ICP_FIT_AND_LEGACY_QUALIFIED = "icp_fit_and_legacy_qualified"
    ICP_FIT_PRESERVES_LEGACY_UNQUALIFIED = (
        "icp_fit_preserves_legacy_unqualified"
    )
    REQUIRED_INFORMATION_MISSING = "required_information_missing"
    ICP_EVIDENCE_INSUFFICIENT = "icp_evidence_insufficient"
    CONFIRMED_EVIDENCE_CONFLICT = "confirmed_evidence_conflict"
    PLAYBOOK_NOT_CONFIGURED = "playbook_not_configured"
    PLAYBOOK_INVALID = "playbook_invalid"
    ICP_ASSESSMENT_INVALID = "icp_assessment_invalid"


class QualificationAuthorityNextAction(StrEnum):
    CONTINUE_EXISTING_FLOW = "continue_existing_flow"
    STOP_QUALIFICATION = "stop_qualification"
    COLLECT_MORE_INFORMATION = "collect_more_information"


@dataclass(frozen=True, slots=True)
class QualificationAuthorityDecision:
    decision: QualificationAuthorityDecisionValue
    reason_codes: tuple[QualificationAuthorityReasonCode, ...]
    legacy_outcome: str
    icp_fit_status: ICPFitStatus | None
    icp_authoritative: bool
    more_information_required: bool
    next_action: QualificationAuthorityNextAction
    resulting_lead_status: LeadStatus
    qualified_for_downstream: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "policy_version": QUALIFICATION_AUTHORITY_POLICY_VERSION,
            "decision": self.decision.value,
            "reason_codes": [item.value for item in self.reason_codes],
            "legacy_outcome": self.legacy_outcome,
            "icp_fit_status": (
                self.icp_fit_status.value
                if self.icp_fit_status is not None
                else None
            ),
            "icp_authoritative": self.icp_authoritative,
            "more_information_required": self.more_information_required,
            "next_action": self.next_action.value,
            "resulting_lead_status": self.resulting_lead_status.value,
            "qualified_for_downstream": self.qualified_for_downstream,
        }


class QualificationDecisionPolicy:
    """Resolve legacy and validated ICP outcomes without side effects."""

    def decide(
        self,
        *,
        legacy_qualified: bool,
        icp_assessment: object,
        current_lead_status: LeadStatus,
    ) -> QualificationAuthorityDecision:
        legacy_outcome = "qualified" if legacy_qualified else "unqualified"
        assessment = self._assessment(icp_assessment)
        if assessment is None:
            return self._preserve_legacy(
                legacy_qualified,
                QualificationAuthorityReasonCode.ICP_ASSESSMENT_INVALID,
            )

        status = assessment["status"]
        if status == "unavailable":
            reason = self._unavailable_reason(assessment.get("reason_code"))
            return self._preserve_legacy(legacy_qualified, reason)
        if status != "assessed":
            return self._preserve_legacy(
                legacy_qualified,
                QualificationAuthorityReasonCode.ICP_ASSESSMENT_INVALID,
            )

        try:
            fit_status = ICPFitStatus(assessment["fit_status"])
        except (KeyError, TypeError, ValueError):
            return self._preserve_legacy(
                legacy_qualified,
                QualificationAuthorityReasonCode.ICP_ASSESSMENT_INVALID,
            )

        if fit_status is ICPFitStatus.DISQUALIFIED:
            if (
                not self._has_top_level_reason(
                    assessment,
                    ICPReasonCode.CONFIRMED_DISQUALIFIER_MATCHED,
                )
                or not self._confirmed_assessments(
                    assessment.get("matched_disqualifiers"),
                    ICPReasonCode.CONFIRMED_VALUE_MATCH,
                )
            ):
                return self._preserve_legacy(
                    legacy_qualified,
                    QualificationAuthorityReasonCode.ICP_ASSESSMENT_INVALID,
                )
            return self._decision(
                QualificationAuthorityDecisionValue.UNQUALIFIED,
                QualificationAuthorityReasonCode.CONFIRMED_DISQUALIFIER_MATCHED,
                legacy_outcome,
                fit_status,
                authoritative=True,
                more_information=False,
                next_action=QualificationAuthorityNextAction.STOP_QUALIFICATION,
                lead_status=LeadStatus.UNQUALIFIED,
                qualified=False,
            )

        if fit_status is ICPFitStatus.NOT_FIT:
            if (
                not self._has_top_level_reason(
                    assessment,
                    ICPReasonCode.REQUIRED_CRITERION_MISMATCH,
                )
                or not self._confirmed_assessments(
                    assessment.get("mismatched_criteria"),
                    ICPReasonCode.CONFIRMED_VALUE_MISMATCH,
                )
            ):
                return self._preserve_legacy(
                    legacy_qualified,
                    QualificationAuthorityReasonCode.ICP_ASSESSMENT_INVALID,
                )
            return self._decision(
                QualificationAuthorityDecisionValue.UNQUALIFIED,
                QualificationAuthorityReasonCode.CONFIRMED_REQUIRED_CRITERION_MISMATCH,
                legacy_outcome,
                fit_status,
                authoritative=True,
                more_information=False,
                next_action=QualificationAuthorityNextAction.STOP_QUALIFICATION,
                lead_status=LeadStatus.UNQUALIFIED,
                qualified=False,
            )

        if fit_status is ICPFitStatus.INSUFFICIENT_INFORMATION:
            if not self._has_top_level_reason(
                assessment,
                ICPReasonCode.REQUIRED_CRITERION_UNKNOWN,
            ):
                return self._preserve_legacy(
                    legacy_qualified,
                    QualificationAuthorityReasonCode.ICP_ASSESSMENT_INVALID,
                )
            reason = (
                QualificationAuthorityReasonCode.CONFIRMED_EVIDENCE_CONFLICT
                if self._has_reason(
                    assessment.get("unknown_criteria"),
                    ICPReasonCode.CONFLICTING_EVIDENCE,
                )
                else QualificationAuthorityReasonCode.ICP_EVIDENCE_INSUFFICIENT
            )
            return self._needs_more_information(
                legacy_outcome,
                fit_status,
                current_lead_status,
                reason,
            )

        if not self._has_top_level_reason(
            assessment,
            ICPReasonCode.ALL_REQUIRED_CRITERIA_MATCHED,
        ):
            return self._preserve_legacy(
                legacy_qualified,
                QualificationAuthorityReasonCode.ICP_ASSESSMENT_INVALID,
            )
        required_gaps = assessment.get("required_information_gaps")
        if not isinstance(required_gaps, list):
            return self._preserve_legacy(
                legacy_qualified,
                QualificationAuthorityReasonCode.ICP_ASSESSMENT_INVALID,
            )
        if required_gaps:
            return self._needs_more_information(
                legacy_outcome,
                fit_status,
                current_lead_status,
                QualificationAuthorityReasonCode.REQUIRED_INFORMATION_MISSING,
            )
        if legacy_qualified:
            return self._decision(
                QualificationAuthorityDecisionValue.QUALIFIED,
                QualificationAuthorityReasonCode.ICP_FIT_AND_LEGACY_QUALIFIED,
                legacy_outcome,
                fit_status,
                authoritative=True,
                more_information=False,
                next_action=QualificationAuthorityNextAction.CONTINUE_EXISTING_FLOW,
                lead_status=LeadStatus.QUALIFIED,
                qualified=True,
            )
        return self._preserve_legacy(
            legacy_qualified,
            QualificationAuthorityReasonCode.ICP_FIT_PRESERVES_LEGACY_UNQUALIFIED,
            fit_status=fit_status,
        )

    @staticmethod
    def _assessment(value: object) -> dict[str, object] | None:
        if not isinstance(value, dict) or not isinstance(value.get("status"), str):
            return None
        return value

    @staticmethod
    def _unavailable_reason(value: object) -> QualificationAuthorityReasonCode:
        return {
            QualificationAuthorityReasonCode.PLAYBOOK_NOT_CONFIGURED.value: (
                QualificationAuthorityReasonCode.PLAYBOOK_NOT_CONFIGURED
            ),
            QualificationAuthorityReasonCode.PLAYBOOK_INVALID.value: (
                QualificationAuthorityReasonCode.PLAYBOOK_INVALID
            ),
        }.get(value, QualificationAuthorityReasonCode.ICP_ASSESSMENT_INVALID)

    @staticmethod
    def _confirmed_assessments(
        value: object,
        reason: ICPReasonCode,
    ) -> bool:
        return (
            isinstance(value, list)
            and bool(value)
            and all(
                isinstance(item, dict)
                and item.get("reason_code") == reason.value
                for item in value
            )
        )

    @staticmethod
    def _has_reason(value: object, reason: ICPReasonCode) -> bool:
        return isinstance(value, list) and any(
            isinstance(item, dict) and item.get("reason_code") == reason.value
            for item in value
        )

    @staticmethod
    def _has_top_level_reason(
        assessment: dict[str, object],
        reason: ICPReasonCode,
    ) -> bool:
        values = assessment.get("reason_codes")
        return isinstance(values, list) and reason.value in values

    def _preserve_legacy(
        self,
        legacy_qualified: bool,
        reason: QualificationAuthorityReasonCode,
        *,
        fit_status: ICPFitStatus | None = None,
    ) -> QualificationAuthorityDecision:
        return self._decision(
            QualificationAuthorityDecisionValue.PRESERVE_LEGACY,
            reason,
            "qualified" if legacy_qualified else "unqualified",
            fit_status,
            authoritative=False,
            more_information=False,
            next_action=(
                QualificationAuthorityNextAction.CONTINUE_EXISTING_FLOW
                if legacy_qualified
                else QualificationAuthorityNextAction.STOP_QUALIFICATION
            ),
            lead_status=(
                LeadStatus.QUALIFIED if legacy_qualified else LeadStatus.UNQUALIFIED
            ),
            qualified=legacy_qualified,
        )

    def _needs_more_information(
        self,
        legacy_outcome: str,
        fit_status: ICPFitStatus,
        current_lead_status: LeadStatus,
        reason: QualificationAuthorityReasonCode,
    ) -> QualificationAuthorityDecision:
        return self._decision(
            QualificationAuthorityDecisionValue.NEEDS_MORE_INFORMATION,
            reason,
            legacy_outcome,
            fit_status,
            authoritative=True,
            more_information=True,
            next_action=QualificationAuthorityNextAction.COLLECT_MORE_INFORMATION,
            lead_status=current_lead_status,
            qualified=False,
        )

    @staticmethod
    def _decision(
        decision: QualificationAuthorityDecisionValue,
        reason: QualificationAuthorityReasonCode,
        legacy_outcome: str,
        fit_status: ICPFitStatus | None,
        *,
        authoritative: bool,
        more_information: bool,
        next_action: QualificationAuthorityNextAction,
        lead_status: LeadStatus,
        qualified: bool,
    ) -> QualificationAuthorityDecision:
        return QualificationAuthorityDecision(
            decision=decision,
            reason_codes=(reason,),
            legacy_outcome=legacy_outcome,
            icp_fit_status=fit_status,
            icp_authoritative=authoritative,
            more_information_required=more_information,
            next_action=next_action,
            resulting_lead_status=lead_status,
            qualified_for_downstream=qualified,
        )
