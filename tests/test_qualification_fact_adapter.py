from uuid import uuid4

import pytest
from sqlmodel import Session, select

from app.core.events import Department as DepartmentKind
from app.core.work_items import WorkItemStatus
from app.departments.sales.evidence import SalesEvidenceClassification
from app.departments.sales.icp_scoring import ICPFitStatus, evaluate_icp
from app.departments.sales.playbook import SalesPlaybookV1
from app.models import (
    ApprovalRequest,
    Department,
    Lead,
    LeadResearch,
    OutboundIntegrationAction,
    WorkItem,
    Workspace,
)
from app.services.qualification_facts import (
    QualificationFactAdapter,
    QualificationFactScopeError,
)


def playbook(
    *criterion_types: str,
    required_information: list[dict[str, str]] | None = None,
) -> SalesPlaybookV1:
    criteria = [
        {
            "key": f"target_{criterion_type}",
            "criterion_type": criterion_type,
            "operator": "gte" if criterion_type in {"company_size", "channel_volume"} else "equals",
            "values": [10] if criterion_type in {"company_size", "channel_volume"} else ["tn"],
            "importance": "required",
        }
        for criterion_type in criterion_types
    ]
    return SalesPlaybookV1.model_validate(
        {
            "schema_version": 1,
            "icp": {"criteria": criteria, "disqualifiers": []},
            "qualification": {
                "required_information": required_information or [],
            },
        }
    )


def evidence(
    criterion_type: str,
    value: object,
    *,
    key: str | None = None,
    classification: str = "confirmed",
) -> dict[str, object]:
    return {
        "type": "qualification_fact",
        "schema_version": 1,
        "key": key or criterion_type,
        "criterion_type": criterion_type,
        "classification": classification,
        "value": value,
    }


def persisted_state(
    session: Session,
    policy: SalesPlaybookV1,
    *items: dict[str, object],
    slug: str | None = None,
) -> tuple[Workspace, Lead, LeadResearch]:
    workspace = Workspace(
        slug=slug or f"facts-{uuid4().hex}",
        name="Qualification Facts",
        sales_playbook=policy.model_dump(mode="json"),
    )
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    lead = Lead(
        tenant_id=workspace.slug,
        full_name="Prospect",
        company_name="Acme",
        score=37,
    )
    session.add(lead)
    session.commit()
    session.refresh(lead)
    research = LeadResearch(
        lead_id=lead.id,
        summary="Persisted research",
        evidence=list(items),
    )
    session.add(research)
    session.commit()
    session.refresh(research)
    return workspace, lead, research


def test_confirmed_country_from_typed_persisted_research_evidence(session: Session) -> None:
    workspace, lead, research = persisted_state(
        session,
        playbook("country"),
        evidence("country", "TN"),
    )

    source = QualificationFactAdapter(session).build_icp_input(
        workspace,
        lead.id,
        research_id=research.id,
    )

    assert len(source.facts) == 1
    assert source.facts[0].classification is SalesEvidenceClassification.CONFIRMED
    assert source.facts[0].value == "tn"


def test_requested_criterion_without_data_is_explicitly_unknown(session: Session) -> None:
    workspace, lead, _ = persisted_state(session, playbook("company_size"))

    source = QualificationFactAdapter(session).build_icp_input(workspace, lead.id)

    assert len(source.facts) == 1
    assert source.facts[0].criterion_type.value == "company_size"
    assert source.facts[0].classification is SalesEvidenceClassification.UNKNOWN
    assert source.facts[0].value is None
    assert source.facts[0].source_reference is None


def test_inference_is_preserved_and_never_promoted(session: Session) -> None:
    workspace, lead, research = persisted_state(
        session,
        playbook("business_problem"),
        evidence(
            "business_problem",
            "slow customer response",
            classification="inference",
        ),
    )

    source = QualificationFactAdapter(session).build_icp_input(
        workspace,
        lead.id,
        research_id=research.id,
    )

    assert source.facts[0].classification is SalesEvidenceClassification.INFERENCE
    assert all(
        fact.classification is not SalesEvidenceClassification.CONFIRMED
        for fact in source.facts
    )


def test_conflicting_confirmed_values_are_both_preserved(session: Session) -> None:
    workspace, lead, research = persisted_state(
        session,
        playbook("country"),
        evidence("country", "tn"),
        evidence("country", "fr"),
    )

    source = QualificationFactAdapter(session).build_icp_input(
        workspace,
        lead.id,
        research_id=research.id,
    )

    assert {fact.value for fact in source.facts} == {"tn", "fr"}
    result = evaluate_icp(playbook("country"), source)
    assert result.fit_status is ICPFitStatus.INSUFFICIENT_INFORMATION
    assert result.unknown_criteria[0].reason_code.value == "conflicting_evidence"


def test_unsupported_or_malformed_research_evidence_remains_unknown(
    session: Session,
) -> None:
    malformed = {"type": "lead_input", "field": "company_size", "value": "50"}
    invalid_typed = evidence("company_size", "50")
    workspace, lead, research = persisted_state(
        session,
        playbook("company_size"),
        malformed,
        invalid_typed,
    )

    source = QualificationFactAdapter(session).build_icp_input(
        workspace,
        lead.id,
        research_id=research.id,
    )

    assert len(source.facts) == 1
    assert source.facts[0].classification is SalesEvidenceClassification.UNKNOWN


def test_adapter_selects_only_playbook_requested_facts(session: Session) -> None:
    workspace, lead, research = persisted_state(
        session,
        playbook("country"),
        evidence("country", "tn"),
        evidence("industry", "saas"),
        evidence("company_size", 50),
    )

    source = QualificationFactAdapter(session).build_icp_input(
        workspace,
        lead.id,
        research_id=research.id,
    )

    assert [fact.criterion_type.value for fact in source.facts] == ["country"]


def test_required_information_key_can_select_a_typed_fact(session: Session) -> None:
    policy = playbook(
        required_information=[
            {"key": "business_need", "description": "Confirmed business need"}
        ]
    )
    workspace, lead, research = persisted_state(
        session,
        policy,
        evidence(
            "business_problem",
            "slow customer response",
            key="business_need",
        ),
    )

    source = QualificationFactAdapter(session).build_icp_input(
        workspace,
        lead.id,
        research_id=research.id,
    )

    assert source.facts[0].key == "business_need"


def test_provenance_is_generated_and_never_contains_business_content(
    session: Session,
) -> None:
    secret_like_value = "private-business-value"
    workspace, lead, research = persisted_state(
        session,
        playbook("use_case"),
        evidence("use_case", secret_like_value),
    )

    source = QualificationFactAdapter(session).build_icp_input(
        workspace,
        lead.id,
        research_id=research.id,
    )

    reference = source.facts[0].source_reference
    assert reference == f"lead_research:{research.id}:evidence:0"
    assert secret_like_value not in reference


def test_cross_workspace_lead_is_rejected(session: Session) -> None:
    first, _, _ = persisted_state(session, playbook("country"), slug="facts-first")
    _, foreign_lead, _ = persisted_state(
        session,
        playbook("country"),
        slug="facts-second",
    )

    with pytest.raises(QualificationFactScopeError):
        QualificationFactAdapter(session).build_icp_input(first, foreign_lead.id)


def test_cross_workspace_research_is_rejected(session: Session) -> None:
    workspace, lead, _ = persisted_state(
        session,
        playbook("country"),
        slug="research-first",
    )
    _, _, foreign_research = persisted_state(
        session,
        playbook("country"),
        slug="research-second",
    )

    with pytest.raises(QualificationFactScopeError):
        QualificationFactAdapter(session).build_icp_input(
            workspace,
            lead.id,
            research_id=foreign_research.id,
        )


def test_adapter_is_read_only_and_creates_no_execution_side_effects(
    session: Session,
    monkeypatch,
) -> None:
    workspace, lead, research = persisted_state(
        session,
        playbook("country"),
        evidence("country", "tn"),
    )
    department = Department(workspace_id=workspace.id, kind=DepartmentKind.SALES)
    session.add(department)
    session.commit()
    session.refresh(department)
    work_item = WorkItem(
        workspace_id=workspace.id,
        department_id=department.id,
        work_type="qualify_lead",
        title="Existing qualification",
        status=WorkItemStatus.ASSIGNED,
    )
    session.add(work_item)
    session.commit()
    session.refresh(work_item)
    original = (lead.score, lead.status, lead.sales_stage, research.evidence)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Adapter attempted an external or AI call")

    monkeypatch.setattr("httpx.Client.request", forbidden)
    source = QualificationFactAdapter(session).build_icp_input(
        workspace,
        lead.id,
        research_id=research.id,
    )
    session.refresh(lead)
    session.refresh(research)
    session.refresh(work_item)

    assert source.workspace_id == workspace.id
    assert source.lead_id == lead.id
    assert (lead.score, lead.status, lead.sales_stage, research.evidence) == original
    assert WorkItemStatus(work_item.status) is WorkItemStatus.ASSIGNED
    assert work_item.result is None
    assert list(session.exec(select(ApprovalRequest)).all()) == []
    assert list(session.exec(select(OutboundIntegrationAction)).all()) == []


def test_output_is_directly_compatible_with_icp_scoring_input(session: Session) -> None:
    policy = playbook("country")
    workspace, lead, research = persisted_state(
        session,
        policy,
        evidence("country", "tn"),
    )

    source = QualificationFactAdapter(session).build_icp_input(
        workspace,
        lead.id,
        research_id=research.id,
    )
    result = evaluate_icp(policy, source)

    assert result.fit_status is ICPFitStatus.FIT
