from collections.abc import Iterator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import Settings
from app.core.agent_skills import AgentSkillRoleNotEligibleError
from app.core.ai_employees import AIEmployeeRoleKey
from app.core.capabilities import BusinessCapabilityKey
from app.core.work_items import WorkItemStatus
from app.departments.sales.icp_scoring import ICPFitStatus
from app.departments.sales.playbook import SalesPlaybookV1
from app.departments.sales.services.work_item_execution import (
    SalesWorkItemExecutionScopeError,
    SalesWorkItemExecutionService,
    SalesWorkItemExecutionStateError,
)
from app.models import (
    AIEmployee,
    AIInvocationUsage,
    ApprovalRequest,
    FollowUpTask,
    Lead,
    LeadResearch,
    LeadStatus,
    OutboundIntegrationAction,
    SalesStage,
    WorkItem,
    Workspace,
)
from app.services.ai_employee_capability_assignments import (
    AIEmployeeCapabilityAssignmentService,
)
from app.services.ai_employees import AIEmployeeService
from app.services.capabilities import CapabilityService
from app.services.departments import DepartmentService
from app.services.work_items import WorkItemNotFoundError, WorkItemService


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield session
    finally:
        SQLModel.metadata.drop_all(engine)
        engine.dispose()


def settings() -> Settings:
    return Settings(
        environment="test",
        database_url="sqlite://",
        llm_mode="demo",
    )


def rule(value: str) -> dict[str, object]:
    return {
        "key": "target_problem",
        "criterion_type": "business_problem",
        "operator": "equals",
        "values": [value],
        "importance": "required",
    }


def disqualifier(value: str) -> dict[str, object]:
    return {
        "key": "excluded_problem",
        "criterion_type": "business_problem",
        "operator": "equals",
        "values": [value],
    }


def playbook(
    *,
    criteria: list[dict[str, object]] | None = None,
    disqualifiers: list[dict[str, object]] | None = None,
) -> SalesPlaybookV1:
    return SalesPlaybookV1.model_validate(
        {
            "schema_version": 1,
            "icp": {
                "criteria": criteria or [],
                "disqualifiers": disqualifiers or [],
            },
            "qualification": {"required_information": []},
        }
    )


def fact(
    value: str,
    *,
    classification: str = "confirmed",
) -> dict[str, object]:
    return {
        "type": "qualification_fact",
        "schema_version": 1,
        "key": "business_problem",
        "criterion_type": "business_problem",
        "classification": classification,
        "value": value,
    }


def qualification_work_item(
    session: Session,
    slug: str,
    *,
    policy: SalesPlaybookV1 | dict[str, object] | None,
    evidence: list[dict[str, object]],
) -> tuple[Workspace, Lead, LeadResearch, WorkItem, AIEmployee]:
    workspace = Workspace(
        slug=slug,
        name=slug,
        sales_playbook=(
            policy.model_dump(mode="json")
            if isinstance(policy, SalesPlaybookV1)
            else policy
        ),
    )
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    department = DepartmentService(session).ensure_sales_department(workspace)
    capability = CapabilityService(session).ensure_for_department(
        workspace,
        department,
        BusinessCapabilityKey.QUALIFY_LEAD,
    )
    employee = AIEmployeeService(session).create_for_department(
        workspace,
        department,
        AIEmployeeRoleKey.QUALIFICATION,
        name="Qualification specialist",
    )
    assignment = AIEmployeeCapabilityAssignmentService(session).assign(
        workspace,
        employee,
        capability,
    )
    lead = Lead(
        tenant_id=workspace.slug,
        full_name="Prospect",
        company_name="Acme",
        job_title="Sales Director",
        email="prospect@example.test",
        website="https://example.test",
        notes="Needs a reliable customer response process.",
    )
    session.add(lead)
    session.commit()
    session.refresh(lead)
    research = LeadResearch(
        lead_id=lead.id,
        summary="Existing persisted research",
        opportunities=["Improve response speed"],
        evidence=evidence,
    )
    session.add(research)
    session.commit()
    session.refresh(research)
    work_item = WorkItemService(session).create_work_item(
        workspace,
        department,
        work_type=BusinessCapabilityKey.QUALIFY_LEAD.value,
        title="Qualify researched lead",
        input={
            "lead_id": str(lead.id),
            "lead_research_id": str(research.id),
        },
        capability=capability,
    )
    work_item = WorkItemService(session).assign_work_item(
        workspace,
        work_item.id,
        assignment,
    )
    return workspace, lead, research, work_item, employee


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy", "evidence", "fit_status"),
    [
        (
            playbook(criteria=[rule("slow response")]),
            [fact("slow response")],
            ICPFitStatus.FIT,
        ),
        (
            playbook(criteria=[rule("fast response")]),
            [fact("slow response")],
            ICPFitStatus.NOT_FIT,
        ),
        (
            playbook(disqualifiers=[disqualifier("slow response")]),
            [fact("slow response")],
            ICPFitStatus.DISQUALIFIED,
        ),
        (
            playbook(criteria=[rule("slow response")]),
            [{"type": "legacy_research", "value": "slow response"}],
            ICPFitStatus.INSUFFICIENT_INFORMATION,
        ),
    ],
)
async def test_real_qualification_work_item_persists_observational_icp_assessment(
    session: Session,
    policy: SalesPlaybookV1,
    evidence: list[dict[str, object]],
    fit_status: ICPFitStatus,
) -> None:
    workspace, lead, _, work_item, _ = qualification_work_item(
        session,
        f"qualification-assessment-{fit_status.value}",
        policy=policy,
        evidence=evidence,
    )
    original_stage = lead.sales_stage

    completed = await SalesWorkItemExecutionService(
        session,
        settings(),
    ).execute(workspace, work_item.id)

    assert completed.status == WorkItemStatus.COMPLETED
    assert completed.result is not None
    assessment = completed.result["icp_assessment"]
    assert set(assessment) == {
        "status",
        "skill",
        "matched_criteria",
        "mismatched_criteria",
        "unknown_criteria",
        "matched_disqualifiers",
        "unmatched_disqualifiers",
        "unknown_disqualifiers",
        "known_required_information",
        "required_information_gaps",
        "fit_status",
        "reason_codes",
        "evidence_summary",
    }
    assert assessment["status"] == "assessed"
    assert assessment["fit_status"] == fit_status.value
    assert assessment["skill"] == {
        "key": "icp_scoring",
        "version": "v1",
        "attribution_identifier": "sales.icp_scoring.v1",
        "ai_invoked": False,
    }
    evidence_summary = assessment["evidence_summary"]
    assert evidence_summary["total"] == (
        evidence_summary["confirmed"]
        + evidence_summary["inference"]
        + evidence_summary["unknown"]
    )
    assert completed.result["score"] == 85
    assert completed.result["qualified"] is True
    assert completed.result["outcome"] == "qualified"
    session.refresh(lead)
    assert lead.status == LeadStatus.QUALIFIED
    assert lead.score == 85
    assert lead.sales_stage == original_stage == SalesStage.INTRODUCTION
    assert session.exec(select(FollowUpTask)).all() == []
    assert session.exec(select(OutboundIntegrationAction)).all() == []
    assert session.exec(select(ApprovalRequest)).all() == []
    assert session.exec(select(AIInvocationUsage)).all() == []
    assert len(session.exec(select(WorkItem)).all()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy", "reason_code"),
    [
        (None, "playbook_not_configured"),
        ({"schema_version": 999}, "playbook_invalid"),
    ],
)
async def test_playbook_unavailable_preserves_legacy_qualification(
    session: Session,
    policy: dict[str, object] | None,
    reason_code: str,
) -> None:
    workspace, lead, _, work_item, _ = qualification_work_item(
        session,
        f"qualification-{reason_code}",
        policy=policy,
        evidence=[fact("slow response")],
    )

    completed = await SalesWorkItemExecutionService(
        session,
        settings(),
    ).execute(workspace, work_item.id)

    assert completed.result is not None
    assert completed.result["icp_assessment"] == {
        "status": "unavailable",
        "reason_code": reason_code,
        "skill": {
            "key": "icp_scoring",
            "version": "v1",
            "attribution_identifier": "sales.icp_scoring.v1",
            "ai_invoked": False,
        },
    }
    assert completed.result["score"] == 85
    assert completed.result["qualified"] is True
    session.refresh(lead)
    assert lead.status == LeadStatus.QUALIFIED
    assert lead.score == 85


@pytest.mark.asyncio
async def test_fit_assessment_does_not_override_legacy_unqualified_result(
    session: Session,
) -> None:
    workspace, lead, research, work_item, _ = qualification_work_item(
        session,
        "qualification-fit-not-authoritative",
        policy=playbook(criteria=[rule("slow response")]),
        evidence=[fact("slow response")],
    )
    lead.email = None
    lead.website = None
    lead.job_title = None
    lead.notes = None
    research.opportunities = []
    session.add(lead)
    session.add(research)
    session.commit()

    completed = await SalesWorkItemExecutionService(
        session,
        settings(),
    ).execute(workspace, work_item.id)

    assert completed.result is not None
    assert completed.result["icp_assessment"]["fit_status"] == "fit"
    assert completed.result["qualified"] is False
    assert completed.result["score"] == 10
    session.refresh(lead)
    assert lead.status == LeadStatus.UNQUALIFIED
    assert lead.score == 10


@pytest.mark.asyncio
async def test_cross_workspace_work_item_and_research_fail_closed(
    session: Session,
) -> None:
    first, first_lead, _, work_item, _ = qualification_work_item(
        session,
        "qualification-scope-first",
        policy=playbook(criteria=[rule("slow response")]),
        evidence=[fact("slow response")],
    )
    second, foreign_lead, foreign_research, _, _ = qualification_work_item(
        session,
        "qualification-scope-second",
        policy=playbook(criteria=[rule("slow response")]),
        evidence=[fact("slow response")],
    )

    with pytest.raises(WorkItemNotFoundError):
        await SalesWorkItemExecutionService(session, settings()).execute(
            second,
            work_item.id,
        )
    session.refresh(work_item)
    assert WorkItemStatus(work_item.status) is WorkItemStatus.ASSIGNED

    work_item.input = {
        "lead_id": str(foreign_lead.id),
        "lead_research_id": str(foreign_research.id),
    }
    session.add(work_item)
    session.commit()
    with pytest.raises(SalesWorkItemExecutionScopeError):
        await SalesWorkItemExecutionService(session, settings()).execute(
            first,
            work_item.id,
        )
    session.refresh(work_item)
    assert WorkItemStatus(work_item.status) is WorkItemStatus.ASSIGNED

    work_item.input = {
        "lead_id": str(first_lead.id),
        "lead_research_id": str(foreign_research.id),
    }
    session.add(work_item)
    session.commit()
    with pytest.raises(SalesWorkItemExecutionScopeError):
        await SalesWorkItemExecutionService(session, settings()).execute(
            first,
            work_item.id,
        )
    session.refresh(work_item)
    session.refresh(first_lead)
    assert WorkItemStatus(work_item.status) is WorkItemStatus.FAILED
    assert first_lead.status == LeadStatus.NEW
    assert first_lead.score == 0


@pytest.mark.asyncio
async def test_qualification_skill_authorization_and_retry_semantics_remain_enforced(
    session: Session,
) -> None:
    workspace, _, _, work_item, employee = qualification_work_item(
        session,
        "qualification-authorization",
        policy=playbook(criteria=[rule("slow response")]),
        evidence=[fact("slow response")],
    )
    employee.role_key = AIEmployeeRoleKey.LEAD_RESEARCH
    session.add(employee)
    session.commit()

    with pytest.raises(AgentSkillRoleNotEligibleError):
        await SalesWorkItemExecutionService(session, settings()).execute(
            workspace,
            work_item.id,
        )
    session.refresh(work_item)
    assert WorkItemStatus(work_item.status) is WorkItemStatus.ASSIGNED

    employee.role_key = AIEmployeeRoleKey.QUALIFICATION
    session.add(employee)
    session.commit()
    completed = await SalesWorkItemExecutionService(session, settings()).execute(
        workspace,
        work_item.id,
    )
    persisted_result = dict(completed.result or {})

    with pytest.raises(SalesWorkItemExecutionStateError):
        await SalesWorkItemExecutionService(session, settings()).execute(
            workspace,
            work_item.id,
        )
    session.refresh(work_item)
    assert work_item.result == persisted_result
    assert len(session.exec(select(WorkItem)).all()) == 1
