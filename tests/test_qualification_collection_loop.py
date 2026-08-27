from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import Settings
from app.core.capabilities import BusinessCapabilityKey
from app.core.work_items import WorkItemStatus
from app.departments.sales.playbook import SalesPlaybookV1
from app.departments.sales.qualification_collection import (
    QUALIFICATION_COLLECTION_POLICY_VERSION,
    build_qualification_collection_plan,
    conversation_qualification_facts,
    first_collection_context,
)
from app.departments.sales.services.conversation_turn_service import (
    SalesConversationTurnInput,
    SalesConversationTurnService,
)
from app.departments.sales.services.work_item_execution import (
    SalesWorkItemExecutionService,
)
from app.models import (
    ApprovalRequest,
    ConversationMessage,
    FollowUpTask,
    Lead,
    LeadResearch,
    OutboundIntegrationAction,
    WorkItem,
    Workspace,
)
from app.services.department_supervisors import DepartmentSupervisorRoutingService
from app.services.departments import DepartmentService
from app.services.qualification_collection import QualificationCollectionService
from app.services.repository import SalesRepository
from app.services.sales_workforce import SalesWorkforceProvisioningService
from app.services.work_items import WorkItemService


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


class RecordingGateway:
    def __init__(self) -> None:
        self.requests: list[object] = []

    async def invoke(self, request):
        self.requests.append(request)
        return SimpleNamespace(content="I can help with that.")


def _settings(*, demo: bool = True) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "database_url": "sqlite://",
        "llm_mode": "demo" if demo else "openai_compatible",
        "require_human_approval": False,
    }
    if not demo:
        values["llm_api_key"] = "test-key"
    return Settings(**values)


def _playbook(
    *,
    company_size: int = 50,
    include_channel_volume: bool = False,
    preferred: bool = False,
    include_required_information: bool = True,
) -> SalesPlaybookV1:
    criteria: list[dict[str, object]] = [
        {
            "key": "minimum_company_size",
            "criterion_type": "company_size",
            "operator": "gte",
            "values": [company_size],
            "importance": "preferred" if preferred else "required",
        }
    ]
    if include_channel_volume:
        criteria.append(
            {
                "key": "minimum_channel_volume",
                "criterion_type": "channel_volume",
                "operator": "gte",
                "values": [100],
                "importance": "required",
            }
        )
    return SalesPlaybookV1.model_validate(
        {
            "schema_version": 1,
            "icp": {"criteria": criteria, "disqualifiers": []},
            "qualification": {
                "required_information": (
                    [
                        {
                            "key": "company_size",
                            "description": "Confirm the customer company's employee count",
                        }
                    ]
                    if include_required_information
                    else []
                )
            },
        }
    )


async def _initial_qualification(
    session: Session,
    slug: str,
    *,
    playbook: SalesPlaybookV1,
) -> tuple[Workspace, Lead, WorkItem]:
    workspace = Workspace(
        slug=slug,
        name=slug,
        sales_playbook=playbook.model_dump(mode="json"),
    )
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    department = DepartmentService(session).ensure_sales_department(workspace)
    workforce = SalesWorkforceProvisioningService(session).ensure_default_workforce(
        workspace,
        department,
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
        evidence=[],
    )
    session.add(research)
    session.commit()
    session.refresh(research)
    item = WorkItemService(session).create_work_item(
        workspace,
        department,
        work_type=BusinessCapabilityKey.QUALIFY_LEAD.value,
        title="Qualify researched lead",
        input={"lead_id": str(lead.id), "lead_research_id": str(research.id)},
        capability=workforce.capabilities[BusinessCapabilityKey.QUALIFY_LEAD],
    )
    decision = DepartmentSupervisorRoutingService(session).route_and_assign(
        workspace,
        item.id,
    )
    assert decision.routable
    completed = await SalesWorkItemExecutionService(session, _settings()).execute(
        workspace,
        item.id,
    )
    return workspace, lead, completed


def test_collection_plan_is_bounded_and_only_created_for_needs_information() -> None:
    playbook = _playbook()
    assessment = {
        "status": "assessed",
        "unknown_criteria": [
            {
                "rule_key": "minimum_company_size",
                "reason_code": "evidence_missing",
            }
        ],
        "required_information_gaps": [
            {"key": "company_size", "reason_code": "required_information_missing"}
        ],
    }
    needs_policy = {
        "decision": "needs_more_information",
        "reason_codes": ["required_information_missing"],
    }

    plan = build_qualification_collection_plan(
        qualification_work_item_id="qualification-1",
        playbook=playbook,
        icp_assessment=assessment,
        qualification_policy=needs_policy,
    )

    assert plan is not None
    assert plan["policy_version"] == QUALIFICATION_COLLECTION_POLICY_VERSION
    assert plan["collection_status"] == "pending"
    assert first_collection_context(plan).key == "company_size"
    for terminal in ("qualified", "unqualified"):
        assert (
            build_qualification_collection_plan(
                qualification_work_item_id="qualification-1",
                playbook=playbook,
                icp_assessment=assessment,
                qualification_policy={"decision": terminal},
            )
            is None
        )


def test_customer_evidence_requires_explicit_supported_self_report() -> None:
    plan = {
        "collection_status": "pending",
        "missing_required_information": [
            {
                "key": "company_size",
                "description": "Employee count",
                "criterion_type": "company_size",
            }
        ],
        "unresolved_required_criteria": [],
    }

    facts = conversation_qualification_facts(
        plan,
        "We have 75 employees.",
        source_reference="conversation_message:message-1",
    )

    assert len(facts) == 1
    assert facts[0].value == 75
    assert facts[0].source_reference == "conversation_message:message-1"
    assert facts[0].classification.value == "confirmed"
    assert not conversation_qualification_facts(
        plan,
        "I think an AI model would call us a large company.",
        source_reference="conversation_message:message-2",
    )


@pytest.mark.asyncio
async def test_needs_information_persists_plan_and_preferred_only_does_not(
    session: Session,
) -> None:
    _, _, required = await _initial_qualification(
        session,
        "collection-required",
        playbook=_playbook(),
    )
    _, _, preferred = await _initial_qualification(
        session,
        "collection-preferred",
        playbook=_playbook(preferred=True, include_required_information=False),
    )

    assert required.result["outcome"] == "needs_more_information"
    assert required.result["qualification_collection"]["policy_version"] == (
        QUALIFICATION_COLLECTION_POLICY_VERSION
    )
    assert preferred.result["outcome"] == "qualified"
    assert "qualification_collection" not in preferred.result


@pytest.mark.asyncio
async def test_grounded_inbound_message_requalifies_once_and_closes_plan(
    session: Session,
) -> None:
    workspace, lead, original = await _initial_qualification(
        session,
        "collection-qualified",
        playbook=_playbook(),
    )
    gateway = RecordingGateway()
    result = await SalesConversationTurnService(
        repository=SalesRepository(session),
        settings=_settings(demo=False),
        workspace=workspace,
        ai_invocation_gateway=gateway,
    ).process(
        SalesConversationTurnInput(
            lead_id=lead.id,
            channel="website",
            customer_message="We have 75 employees.",
        )
    )
    qualifications = list(
        session.exec(
            select(WorkItem)
            .where(
                WorkItem.workspace_id == workspace.id,
                WorkItem.work_type == BusinessCapabilityKey.QUALIFY_LEAD.value,
            )
            .order_by(WorkItem.created_at.asc())
        ).all()
    )
    inbound = session.exec(
        select(ConversationMessage).where(
            ConversationMessage.lead_id == lead.id,
            ConversationMessage.direction == "inbound",
        )
    ).one()
    requalification = qualifications[-1]

    assert result.ai_invoked is True
    assert len(gateway.requests) == 1
    assert "Pending qualification context" not in gateway.requests[0].user_prompt
    assert len(qualifications) == 2
    assert requalification.parent_work_item_id == original.id
    assert requalification.correlation_id == original.correlation_id
    assert requalification.result["outcome"] == "qualified"
    assert "qualification_collection" not in requalification.result
    references = requalification.result["icp_assessment"]["matched_criteria"][0][
        "evidence_references"
    ]
    assert references == [f"conversation_message:{inbound.id}"]
    assert WorkItemStatus(original.status) is WorkItemStatus.COMPLETED
    assert original.result["qualification_collection"]["collection_status"] == "pending"
    assert not session.exec(select(FollowUpTask)).all()
    assert not session.exec(select(OutboundIntegrationAction)).all()
    assert not session.exec(select(ApprovalRequest)).all()

    duplicate = await QualificationCollectionService(
        session,
        _settings(),
    ).process_persisted_message(workspace, lead, inbound)
    assert duplicate is None
    assert len(
        session.exec(
            select(WorkItem).where(
                WorkItem.workspace_id == workspace.id,
                WorkItem.work_type == BusinessCapabilityKey.QUALIFY_LEAD.value,
            )
        ).all()
    ) == 2


@pytest.mark.asyncio
async def test_unsupported_inbound_gets_bounded_context_but_no_requalification(
    session: Session,
) -> None:
    workspace, lead, _ = await _initial_qualification(
        session,
        "collection-unsupported",
        playbook=_playbook(),
    )
    gateway = RecordingGateway()

    await SalesConversationTurnService(
        repository=SalesRepository(session),
        settings=_settings(demo=False),
        workspace=workspace,
        ai_invocation_gateway=gateway,
    ).process(
        SalesConversationTurnInput(
            lead_id=lead.id,
            channel="website",
            customer_message="Could you explain the product first?",
        )
    )

    assert len(gateway.requests) == 1
    prompt = gateway.requests[0].user_prompt
    assert "Pending qualification context" in prompt
    assert "ask at most one concise question" in prompt
    assert len(
        session.exec(
            select(WorkItem).where(
                WorkItem.workspace_id == workspace.id,
                WorkItem.work_type == BusinessCapabilityKey.QUALIFY_LEAD.value,
            )
        ).all()
    ) == 1


@pytest.mark.asyncio
async def test_requalification_can_authoritatively_unqualify_without_outbound_side_effects(
    session: Session,
) -> None:
    workspace, lead, _ = await _initial_qualification(
        session,
        "collection-unqualified",
        playbook=_playbook(company_size=50),
    )
    gateway = RecordingGateway()

    await SalesConversationTurnService(
        repository=SalesRepository(session),
        settings=_settings(demo=False),
        workspace=workspace,
        ai_invocation_gateway=gateway,
    ).process(
        SalesConversationTurnInput(
            lead_id=lead.id,
            channel="website",
            customer_message="We have 25 employees.",
        )
    )
    latest = session.exec(
        select(WorkItem)
        .where(
            WorkItem.workspace_id == workspace.id,
            WorkItem.work_type == BusinessCapabilityKey.QUALIFY_LEAD.value,
        )
        .order_by(WorkItem.created_at.desc())
    ).first()

    assert latest.result["outcome"] == "unqualified"
    assert "qualification_collection" not in latest.result
    assert not session.exec(select(FollowUpTask)).all()
    assert not session.exec(select(OutboundIntegrationAction)).all()


@pytest.mark.asyncio
async def test_conflicting_customer_evidence_remains_unresolved(
    session: Session,
) -> None:
    workspace, lead, _ = await _initial_qualification(
        session,
        "collection-conflict",
        playbook=_playbook(include_channel_volume=True),
    )
    repository = SalesRepository(session)
    gateway = RecordingGateway()
    service = SalesConversationTurnService(
        repository=repository,
        settings=_settings(demo=False),
        workspace=workspace,
        ai_invocation_gateway=gateway,
    )
    await service.process(
        SalesConversationTurnInput(
            lead_id=lead.id,
            channel="website",
            customer_message="We have 75 employees.",
        )
    )
    await service.process(
        SalesConversationTurnInput(
            lead_id=lead.id,
            channel="website",
            customer_message="Actually, we have 25 employees.",
        )
    )
    latest = session.exec(
        select(WorkItem)
        .where(
            WorkItem.workspace_id == workspace.id,
            WorkItem.work_type == BusinessCapabilityKey.QUALIFY_LEAD.value,
        )
        .order_by(WorkItem.created_at.desc())
    ).first()

    assert latest.result["outcome"] == "needs_more_information"
    assert "confirmed_evidence_conflict" in latest.result["qualification_policy"][
        "reason_codes"
    ]
    assert latest.result["qualification_collection"]["collection_status"] == "pending"


@pytest.mark.asyncio
async def test_foreign_workspace_cannot_read_or_write_collection_lineage(
    session: Session,
) -> None:
    workspace, lead, _ = await _initial_qualification(
        session,
        "collection-owner",
        playbook=_playbook(),
    )
    foreign = Workspace(slug="collection-foreign", name="Foreign")
    session.add(foreign)
    session.commit()
    session.refresh(foreign)
    message = SalesRepository(session).add_message(
        ConversationMessage(
            lead_id=lead.id,
            direction="inbound",
            channel="website",
            content="We have 75 employees.",
        )
    )

    with pytest.raises(PermissionError):
        await QualificationCollectionService(session, _settings()).process_persisted_message(
            foreign,
            lead,
            message,
        )

    assert len(
        session.exec(
            select(WorkItem).where(WorkItem.workspace_id == workspace.id)
        ).all()
    ) == 1
