from dataclasses import replace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlmodel import Session, select

from app.core.agent_skill_execution import AgentSkillExecutionContext
from app.core.ai_employees import AIEmployeeRoleKey
from app.core.capabilities import BusinessCapabilityKey
from app.core.events import Department
from app.core.work_items import WorkItemStatus
from app.departments.sales.evidence import (
    SalesEvidenceClassification,
    SalesEvidenceSourceType,
)
from app.departments.sales.icp_scoring import (
    ICPFact,
    ICPFitStatus,
    ICPReasonCode,
    ICPRequiredInformationStatus,
    ICPRuleStatus,
    ICPScoringAuthorizationError,
    ICPScoringInput,
    ICPScoringOutputValidator,
    ICPScoringValidationError,
    evaluate_icp,
    execute_icp_scoring,
    icp_scoring_components,
)
from app.departments.sales.playbook import (
    SalesPlaybookCriterionType,
    SalesPlaybookV1,
)
from app.departments.sales.skills import sales_agent_skill_registry
from app.models import (
    ApprovalRequest,
    Lead,
    OutboundIntegrationAction,
    WorkItem,
    Workspace,
)
from app.models import (
    Department as DepartmentModel,
)
from app.services.icp_scoring import (
    ICPScoringUnavailableError,
    WorkspaceICPScoringService,
)
from app.services.sales_playbooks import WorkspaceSalesPlaybookPersistenceError


def playbook(
    *,
    criteria: list[dict[str, object]] | None = None,
    disqualifiers: list[dict[str, object]] | None = None,
    required_information: list[dict[str, str]] | None = None,
) -> SalesPlaybookV1:
    return SalesPlaybookV1.model_validate(
        {
            "schema_version": 1,
            "icp": {
                "criteria": criteria or [],
                "disqualifiers": disqualifiers or [],
            },
            "qualification": {
                "required_information": required_information or [],
            },
        }
    )


def rule(
    key: str,
    criterion_type: str,
    operator: str,
    values: list[object],
    *,
    importance: str = "required",
) -> dict[str, object]:
    return {
        "key": key,
        "criterion_type": criterion_type,
        "operator": operator,
        "values": values,
        "importance": importance,
    }


def disqualifier(
    key: str,
    criterion_type: str,
    operator: str,
    values: list[object],
) -> dict[str, object]:
    value = rule(key, criterion_type, operator, values)
    value.pop("importance")
    return value


def fact(
    key: str,
    criterion_type: SalesPlaybookCriterionType,
    value: str | float,
    *,
    classification: SalesEvidenceClassification = SalesEvidenceClassification.CONFIRMED,
    reference: str = "lead_research.fact-1",
) -> ICPFact:
    return ICPFact(
        key,
        criterion_type,
        classification,
        value,
        SalesEvidenceSourceType.LEAD_RESEARCH,
        reference,
    )


def unknown_fact(key: str, criterion_type: SalesPlaybookCriterionType) -> ICPFact:
    return ICPFact(
        key,
        criterion_type,
        SalesEvidenceClassification.UNKNOWN,
        None,
        SalesEvidenceSourceType.MISSING,
    )


def scoring_input(*facts: ICPFact, workspace_id=None, lead_id=None) -> ICPScoringInput:
    return ICPScoringInput(
        workspace_id or uuid4(),
        lead_id or uuid4(),
        tuple(facts),
    )


def context(
    workspace_id,
    *,
    role: AIEmployeeRoleKey = AIEmployeeRoleKey.QUALIFICATION,
    capability: BusinessCapabilityKey = BusinessCapabilityKey.QUALIFY_LEAD,
    tools: frozenset[str] = frozenset(),
) -> AgentSkillExecutionContext:
    return AgentSkillExecutionContext(
        workspace_id=workspace_id,
        department_id=uuid4(),
        department=Department.SALES,
        work_item_id=uuid4(),
        ai_employee_id=uuid4(),
        employee_role=role,
        assignment_id=uuid4(),
        capability_id=uuid4(),
        capability=capability,
        skill_key="icp_scoring",
        skill_version="v1",
        input_contract="sales.icp_scoring.input.v1",
        output_contract="sales.icp_scoring.output.v1",
        validator="sales.icp_scoring.output_validator.v1",
        instruction_component="sales.icp_scoring.instruction.v1",
        effective_tool_ceiling=tools,
        attribution_identifier="sales.icp_scoring.v1",
    )


@pytest.mark.parametrize(
    ("criterion_type", "operator", "expected", "fact_value", "status"),
    [
        ("industry", "equals", ["B2B SaaS"], "  b2b   SAAS ", ICPRuleStatus.MATCHED),
        ("country", "in", ["tn", "fr"], "FR", ICPRuleStatus.MATCHED),
        ("customer_type", "equals", ["business"], "consumer", ICPRuleStatus.MISMATCHED),
        ("company_size", "equals", [25], 25, ICPRuleStatus.MATCHED),
        ("company_size", "gte", [10], 10, ICPRuleStatus.MATCHED),
        ("channel_volume", "lte", [1000], 1001, ICPRuleStatus.MISMATCHED),
    ],
)
def test_deterministic_text_and_numeric_operators(
    criterion_type: str,
    operator: str,
    expected: list[object],
    fact_value: str | int,
    status: ICPRuleStatus,
) -> None:
    policy = playbook(criteria=[rule("target", criterion_type, operator, expected)])
    source = scoring_input(
        fact("target", SalesPlaybookCriterionType(criterion_type), fact_value)
    )

    result = evaluate_icp(policy, source)
    assessment = (*result.matched_criteria, *result.mismatched_criteria)[0]

    assert assessment.status is status


def test_required_match_is_fit_and_required_mismatch_is_not_fit() -> None:
    policy = playbook(criteria=[rule("target_country", "country", "equals", ["tn"])])

    matched = evaluate_icp(
        policy,
        scoring_input(fact("country", SalesPlaybookCriterionType.COUNTRY, "tn")),
    )
    mismatched = evaluate_icp(
        policy,
        scoring_input(fact("country", SalesPlaybookCriterionType.COUNTRY, "fr")),
    )

    assert matched.fit_status is ICPFitStatus.FIT
    assert mismatched.fit_status is ICPFitStatus.NOT_FIT
    assert ICPReasonCode.REQUIRED_CRITERION_MISMATCH in mismatched.reason_codes


def test_required_unknown_is_insufficient_and_preferred_mismatch_does_not_fail() -> None:
    required = rule("target_country", "country", "equals", ["tn"])
    preferred = rule(
        "preferred_industry",
        "industry",
        "equals",
        ["saas"],
        importance="preferred",
    )

    unknown = evaluate_icp(playbook(criteria=[required]), scoring_input())
    preferred_mismatch = evaluate_icp(
        playbook(criteria=[required, preferred]),
        scoring_input(
            fact("country", SalesPlaybookCriterionType.COUNTRY, "tn"),
            fact("industry", SalesPlaybookCriterionType.INDUSTRY, "retail"),
        ),
    )

    assert unknown.fit_status is ICPFitStatus.INSUFFICIENT_INFORMATION
    assert unknown.unknown_criteria[0].reason_code is ICPReasonCode.EVIDENCE_MISSING
    assert preferred_mismatch.fit_status is ICPFitStatus.FIT
    assert preferred_mismatch.mismatched_criteria[0].rule_key == "preferred_industry"


def test_confirmed_disqualifier_wins_and_unknown_disqualifier_does_not() -> None:
    policy = playbook(
        criteria=[rule("target_country", "country", "equals", ["tn"])],
        disqualifiers=[disqualifier("excluded_use", "use_case", "equals", ["spam"])],
    )
    confirmed = evaluate_icp(
        policy,
        scoring_input(
            fact("country", SalesPlaybookCriterionType.COUNTRY, "tn"),
            fact("use_case", SalesPlaybookCriterionType.USE_CASE, "spam"),
        ),
    )
    unknown = evaluate_icp(
        policy,
        scoring_input(
            fact("country", SalesPlaybookCriterionType.COUNTRY, "tn"),
            unknown_fact("use_case", SalesPlaybookCriterionType.USE_CASE),
        ),
    )

    assert confirmed.fit_status is ICPFitStatus.DISQUALIFIED
    assert confirmed.matched_disqualifiers[0].rule_key == "excluded_use"
    assert unknown.fit_status is ICPFitStatus.FIT
    assert unknown.unknown_disqualifiers[0].status is ICPRuleStatus.UNKNOWN


def test_inference_neither_matches_criterion_nor_triggers_disqualifier() -> None:
    inferred_country = fact(
        "country",
        SalesPlaybookCriterionType.COUNTRY,
        "tn",
        classification=SalesEvidenceClassification.INFERENCE,
    )
    policy = playbook(
        criteria=[rule("target_country", "country", "equals", ["tn"])],
        disqualifiers=[disqualifier("excluded_country", "country", "equals", ["fr"])],
    )

    result = evaluate_icp(policy, scoring_input(inferred_country))

    assert result.fit_status is ICPFitStatus.INSUFFICIENT_INFORMATION
    assert result.unknown_criteria[0].reason_code is ICPReasonCode.INFERENCE_NOT_CONFIRMED
    assert (
        result.unknown_disqualifiers[0].reason_code
        is ICPReasonCode.INFERENCE_NOT_CONFIRMED
    )


def test_conflicting_confirmed_evidence_is_unknown_and_safe() -> None:
    policy = playbook(criteria=[rule("target_country", "country", "equals", ["tn"])])
    source = scoring_input(
        fact("country", SalesPlaybookCriterionType.COUNTRY, "tn", reference="research.1"),
        fact("country", SalesPlaybookCriterionType.COUNTRY, "fr", reference="research.2"),
    )

    result = evaluate_icp(policy, source)

    assert result.fit_status is ICPFitStatus.INSUFFICIENT_INFORMATION
    assert result.unknown_criteria[0].reason_code is ICPReasonCode.CONFLICTING_EVIDENCE
    assert result.unknown_criteria[0].evidence_references == ("research.1", "research.2")


def test_required_information_reports_known_and_missing_without_asking_questions() -> None:
    policy = playbook(
        required_information=[
            {"key": "business_need", "description": "Confirmed business need"},
            {"key": "decision_authority", "description": "Decision authority"},
        ]
    )
    source = scoring_input(
        fact("business_need", SalesPlaybookCriterionType.BUSINESS_PROBLEM, "slow response")
    )

    result = evaluate_icp(policy, source)

    assert result.known_required_information[0].key == "business_need"
    assert result.known_required_information[0].status is ICPRequiredInformationStatus.KNOWN
    assert result.required_information_gaps[0].key == "decision_authority"
    assert result.required_information_gaps[0].status is ICPRequiredInformationStatus.GAP


def test_unsupported_operator_cannot_reach_execution() -> None:
    with pytest.raises(ValidationError):
        playbook(criteria=[rule("size", "company_size", "in", [10, 20])])


def test_output_validator_rejects_substituted_result() -> None:
    policy = playbook(criteria=[rule("target", "industry", "equals", ["saas"])])
    source = scoring_input(fact("industry", SalesPlaybookCriterionType.INDUSTRY, "saas"))
    result = evaluate_icp(policy, source)

    with pytest.raises(ICPScoringValidationError):
        ICPScoringOutputValidator().validate(
            replace(result, fit_status=ICPFitStatus.NOT_FIT),
            (policy, source),
        )


def test_skill_components_and_execution_are_tool_free_and_attributed() -> None:
    definition = sales_agent_skill_registry().resolve("icp_scoring", "v1")
    components = icp_scoring_components(definition)
    source = scoring_input()
    execution = execute_icp_scoring(
        playbook(),
        source,
        context(source.workspace_id),
    )

    assert definition.allowed_tool_ceiling == frozenset()
    assert components.input_contract is ICPScoringInput
    assert execution.attribution_identifier == "sales.icp_scoring.v1"
    assert execution.ai_invoked is False


@pytest.mark.parametrize(
    ("role", "capability", "tools"),
    [
        (AIEmployeeRoleKey.LEAD_RESEARCH, BusinessCapabilityKey.QUALIFY_LEAD, frozenset()),
        (AIEmployeeRoleKey.QUALIFICATION, BusinessCapabilityKey.RESEARCH_COMPANY, frozenset()),
        (
            AIEmployeeRoleKey.QUALIFICATION,
            BusinessCapabilityKey.QUALIFY_LEAD,
            frozenset({"send_message"}),
        ),
    ],
)
def test_wrong_role_capability_or_tool_context_fails_closed(role, capability, tools) -> None:
    source = scoring_input()

    with pytest.raises(ICPScoringAuthorizationError):
        execute_icp_scoring(
            playbook(),
            source,
            context(source.workspace_id, role=role, capability=capability, tools=tools),
        )


def persisted_workspace(session: Session, *, playbook_value) -> tuple[Workspace, Lead]:
    workspace = Workspace(
        slug=f"icp-{uuid4().hex}",
        name="ICP Workspace",
        sales_playbook=playbook_value,
    )
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    lead = Lead(tenant_id=workspace.slug, full_name="Prospect", company_name="Acme")
    session.add(lead)
    session.commit()
    session.refresh(lead)
    return workspace, lead


def test_workspace_service_reports_absent_playbook_explicitly(session: Session) -> None:
    workspace, lead = persisted_workspace(session, playbook_value=None)
    source = scoring_input(workspace_id=workspace.id, lead_id=lead.id)

    with pytest.raises(ICPScoringUnavailableError) as exc_info:
        WorkspaceICPScoringService(session).score(
            workspace,
            source,
            context(workspace.id),
        )

    assert exc_info.value.reason_code == "playbook_not_configured"


def test_workspace_service_reuses_malformed_playbook_fail_closed_behavior(
    session: Session,
) -> None:
    workspace, lead = persisted_workspace(
        session,
        playbook_value={"schema_version": 999},
    )
    source = scoring_input(workspace_id=workspace.id, lead_id=lead.id)

    with pytest.raises(WorkspaceSalesPlaybookPersistenceError):
        WorkspaceICPScoringService(session).score(
            workspace,
            source,
            context(workspace.id),
        )


def test_workspace_service_blocks_cross_workspace_lead_and_input(session: Session) -> None:
    configured = playbook().model_dump(mode="json")
    workspace, lead = persisted_workspace(session, playbook_value=configured)
    other, other_lead = persisted_workspace(session, playbook_value=configured)

    with pytest.raises(ICPScoringAuthorizationError):
        WorkspaceICPScoringService(session).score(
            workspace,
            scoring_input(workspace_id=other.id, lead_id=lead.id),
            context(workspace.id),
        )
    with pytest.raises(ICPScoringAuthorizationError):
        WorkspaceICPScoringService(session).score(
            workspace,
            scoring_input(workspace_id=workspace.id, lead_id=other_lead.id),
            context(workspace.id),
        )


def test_scoring_has_no_lead_workitem_approval_or_outbound_side_effects(
    session: Session,
) -> None:
    policy = playbook(
        criteria=[rule("target", "industry", "equals", ["saas"])]
    )
    workspace, lead = persisted_workspace(
        session,
        playbook_value=policy.model_dump(mode="json"),
    )
    department = DepartmentModel(workspace_id=workspace.id, kind=Department.SALES)
    session.add(department)
    session.commit()
    session.refresh(department)
    work_item = WorkItem(
        workspace_id=workspace.id,
        department_id=department.id,
        status=WorkItemStatus.ASSIGNED,
        work_type="qualification",
        title="Existing qualification",
    )
    session.add(work_item)
    session.commit()
    session.refresh(work_item)
    original_score = lead.score
    original_status = lead.status
    source = scoring_input(
        fact("industry", SalesPlaybookCriterionType.INDUSTRY, "saas"),
        workspace_id=workspace.id,
        lead_id=lead.id,
    )

    result = WorkspaceICPScoringService(session).score(
        workspace,
        source,
        replace(
            context(workspace.id),
            department_id=department.id,
            work_item_id=work_item.id,
        ),
    )
    session.refresh(lead)
    session.refresh(work_item)

    assert result.result.fit_status is ICPFitStatus.FIT
    assert lead.score == original_score
    assert lead.status == original_status
    assert WorkItemStatus(work_item.status) is WorkItemStatus.ASSIGNED
    assert work_item.result is None
    assert list(session.exec(select(WorkItem)).all()) == [work_item]
    assert list(session.exec(select(ApprovalRequest)).all()) == []
    assert list(session.exec(select(OutboundIntegrationAction)).all()) == []
