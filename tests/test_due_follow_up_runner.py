from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import MethodType, SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import Settings
from app.core.ai_employees import AIEmployeeRoleKey
from app.core.capabilities import BusinessCapabilityKey
from app.models import (
    AIEmployeeCapabilityToolAccess,
    ApprovalRequest,
    FollowUpTask,
    Lead,
    LeadStatus,
    OutboundIntegrationAction,
    OutboundIntegrationDeliveryAttempt,
    SalesHandoffReasonCode,
    WorkItem,
    Workspace,
)
from app.run_due_followups import main as run_due_followups_cli
from app.services.ai_employee_capability_assignments import (
    AIEmployeeCapabilityAssignmentService,
)
from app.services.ai_employees import AIEmployeeService
from app.services.capabilities import CapabilityService
from app.services.departments import DepartmentService
from app.services.due_follow_up_runner import DueFollowUpRunner
from app.services.repository import SalesRepository

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as database_session:
            yield database_session
    finally:
        SQLModel.metadata.drop_all(engine)
        engine.dispose()


def _foundation(
    session: Session,
    slug: str,
    *,
    due_at: datetime = NOW,
    task_status: str = "pending",
    lead_status: LeadStatus = LeadStatus.ENGAGED,
    configured: bool = True,
    reason: str = "Continue the existing proposal conversation",
) -> SimpleNamespace:
    workspace = Workspace(slug=slug, name=f"{slug} workspace")
    session.add(workspace)
    session.commit()
    session.refresh(workspace)

    if configured:
        department = DepartmentService(session).ensure_sales_department(workspace)
        capability = CapabilityService(session).ensure_for_department(
            workspace,
            department,
            BusinessCapabilityKey.FOLLOW_UP_LEAD,
        )
        employee = AIEmployeeService(session).create_for_department(
            workspace,
            department,
            AIEmployeeRoleKey.FOLLOW_UP,
        )
        AIEmployeeCapabilityAssignmentService(session).assign(
            workspace,
            employee,
            capability,
        )

    lead = Lead(
        tenant_id=workspace.slug,
        full_name="Pilot Prospect",
        company_name="Pilot Company",
        status=lead_status,
    )
    task = FollowUpTask(
        lead_id=lead.id,
        due_at=due_at,
        reason=reason,
        status=task_status,
    )
    session.add_all([lead, task])
    session.commit()
    session.refresh(lead)
    session.refresh(task)
    return SimpleNamespace(workspace=workspace, lead=lead, task=task)


def _run(
    session: Session,
    *,
    now: datetime = NOW,
    workspace_id=None,
    limit: int = 100,
):
    return DueFollowUpRunner(session).run(
        now=now,
        workspace_id=workspace_id,
        limit=limit,
    )


def test_exact_due_boundary_materializes_one_governed_work_item(session: Session):
    state = _foundation(session, "due-follow-up-boundary", due_at=NOW)

    summary = _run(session)
    work_items = list(session.exec(select(WorkItem)).all())

    assert summary.as_dict() == {
        "scanned": 1,
        "eligible": 1,
        "materialized": 1,
        "reused": 0,
        "skipped": 0,
        "failed": 0,
        "reason_counts": {"work_item_materialized": 1},
    }
    assert len(work_items) == 1
    assert work_items[0].source_follow_up_task_id == state.task.id
    assert work_items[0].work_type == "sales_follow_up"
    assert state.task.status == "pending"


def test_future_task_is_not_selected(session: Session):
    _foundation(session, "future-follow-up", due_at=NOW + timedelta(microseconds=1))

    summary = _run(session)

    assert summary.scanned == 0
    assert list(session.exec(select(WorkItem)).all()) == []


@pytest.mark.parametrize("task_status", ["cancelled", "completed", "expired"])
def test_non_pending_task_is_never_resurrected(session: Session, task_status: str):
    _foundation(session, f"{task_status}-follow-up", task_status=task_status)

    summary = _run(session)

    assert summary.scanned == 0
    assert list(session.exec(select(WorkItem)).all()) == []


@pytest.mark.parametrize(
    ("lead_status", "reason"),
    [
        (LeadStatus.WON, "lead_status_won"),
        (LeadStatus.LOST, "lead_status_lost"),
        (LeadStatus.UNQUALIFIED, "lead_status_unqualified"),
    ],
)
def test_terminal_lead_uses_existing_follow_up_stop_policy(
    session: Session,
    lead_status: LeadStatus,
    reason: str,
):
    _foundation(session, f"terminal-{lead_status.value}", lead_status=lead_status)

    summary = _run(session)

    assert summary.skipped == 1
    assert summary.reason_counts == {reason: 1}
    assert list(session.exec(select(WorkItem)).all()) == []


def test_active_handoff_defers_task_without_work_or_resolution(session: Session):
    state = _foundation(session, "active-handoff-follow-up")
    handoff = SalesRepository(session).ensure_sales_handoff(
        workspace=state.workspace,
        lead=state.lead,
        reason_code=SalesHandoffReasonCode.HUMAN_REQUESTED,
        explanation="A human operator owns this conversation.",
    )

    summary = _run(session)

    session.refresh(handoff)
    assert summary.skipped == 1
    assert summary.reason_counts == {"active_human_handoff": 1}
    assert list(session.exec(select(WorkItem)).all()) == []
    assert handoff.status == "active"


def test_sequential_and_retried_runs_reuse_one_canonical_work_item(session: Session):
    state = _foundation(session, "idempotent-follow-up")

    first = _run(session)
    second = _run(session)
    third = _run(session)

    items = list(
        session.exec(
            select(WorkItem).where(WorkItem.source_follow_up_task_id == state.task.id)
        ).all()
    )
    assert first.materialized == 1
    assert second.reused == 1
    assert third.reused == 1
    assert len(items) == 1


def test_runner_reuses_work_materialized_by_another_runner(session: Session):
    state = _foundation(session, "competing-follow-up")
    competing_runner = DueFollowUpRunner(session)
    competing_runner.materializer.materialize_due(
        state.workspace,
        state.task.id,
        now=NOW,
    )

    summary = _run(session)

    assert summary.reused == 1
    assert summary.reason_counts == {"existing_work_item_reused": 1}
    assert len(session.exec(select(WorkItem)).all()) == 1


def test_runner_recovers_concurrent_unique_constraint_winner(session: Session):
    state = _foundation(session, "concurrent-follow-up")
    runner = DueFollowUpRunner(session)
    original = runner.materializer.work_items.create_work_item

    def create_then_report_conflict(_service, workspace, department, **kwargs):
        original(workspace, department, **kwargs)
        raise IntegrityError("simulated concurrent insert", {}, RuntimeError())

    runner.materializer.work_items.create_work_item = MethodType(
        create_then_report_conflict,
        runner.materializer.work_items,
    )

    summary = runner.run(now=NOW)

    assert summary.reused == 1
    assert summary.reason_counts == {"existing_work_item_reused": 1}
    items = list(session.exec(select(WorkItem)).all())
    assert len(items) == 1
    assert items[0].source_follow_up_task_id == state.task.id


def test_workspace_scope_never_materializes_foreign_task(session: Session):
    selected = _foundation(session, "runner-workspace-a")
    foreign = _foundation(session, "runner-workspace-b")

    summary = _run(session, workspace_id=selected.workspace.id)
    items = list(session.exec(select(WorkItem)).all())

    assert summary.scanned == 1
    assert len(items) == 1
    assert items[0].workspace_id == selected.workspace.id
    assert items[0].source_follow_up_task_id == selected.task.id
    assert items[0].source_follow_up_task_id != foreign.task.id


def test_batch_limit_is_deterministic_and_bounded(session: Session):
    states = [
        _foundation(
            session,
            f"bounded-follow-up-{index}",
            due_at=NOW - timedelta(minutes=3 - index),
        )
        for index in range(3)
    ]

    summary = _run(session, limit=2)
    item_task_ids = {
        item.source_follow_up_task_id for item in session.exec(select(WorkItem)).all()
    }

    assert summary.scanned == 2
    assert summary.materialized == 2
    assert item_task_ids == {states[0].task.id, states[1].task.id}


def test_one_bad_task_does_not_abort_another_eligible_task(session: Session):
    bad = _foundation(
        session,
        "bad-runner-task",
        due_at=NOW - timedelta(minutes=2),
        configured=False,
    )
    good = _foundation(
        session,
        "good-runner-task",
        due_at=NOW - timedelta(minutes=1),
    )

    summary = _run(session)
    items = list(session.exec(select(WorkItem)).all())

    assert summary.scanned == 2
    assert summary.eligible == 2
    assert summary.failed == 1
    assert summary.materialized == 1
    assert summary.reason_counts["configuration_error"] == 1
    assert len(items) == 1
    assert items[0].source_follow_up_task_id == good.task.id
    assert items[0].source_follow_up_task_id != bad.task.id


def test_unexpected_task_failure_is_bounded_and_batch_continues(session: Session):
    broken = _foundation(
        session,
        "unexpected-runner-failure",
        due_at=NOW - timedelta(minutes=2),
    )
    healthy = _foundation(
        session,
        "unexpected-runner-healthy",
        due_at=NOW - timedelta(minutes=1),
    )
    runner = DueFollowUpRunner(session)
    original = runner.materializer.materialize_due

    def materialize_with_one_failure(_service, workspace, task_id, *, now=None):
        if task_id == broken.task.id:
            raise RuntimeError("private customer text must not escape")
        return original(workspace, task_id, now=now)

    runner.materializer.materialize_due = MethodType(
        materialize_with_one_failure,
        runner.materializer,
    )

    summary = runner.run(now=NOW)

    assert summary.failed == 1
    assert summary.materialized == 1
    assert summary.reason_counts["task_processing_error"] == 1
    assert {
        item.source_follow_up_task_id for item in session.exec(select(WorkItem)).all()
    } == {healthy.task.id}


def test_runner_grants_no_authority_and_never_calls_outbound(session: Session):
    _foundation(session, "runner-no-direct-outbound")

    summary = _run(session)

    assert summary.materialized == 1
    assert list(session.exec(select(ApprovalRequest)).all()) == []
    assert list(session.exec(select(AIEmployeeCapabilityToolAccess)).all()) == []
    assert list(session.exec(select(OutboundIntegrationAction)).all()) == []
    assert list(session.exec(select(OutboundIntegrationDeliveryAttempt)).all()) == []


def test_cli_returns_safe_machine_readable_summary(
    session: Session,
    capsys: pytest.CaptureFixture[str],
):
    state = _foundation(
        session,
        "runner-cli",
        due_at=datetime(2020, 1, 1, tzinfo=UTC),
        reason="PRIVATE CUSTOMER CONVERSATION MUST NOT BE PRINTED",
    )
    settings = Settings(
        environment="test",
        database_url="sqlite://",
        llm_mode="demo",
        log_format="text",
    )

    exit_code = run_due_followups_cli(
        ["--limit", "10", "--workspace-id", str(state.workspace.id)],
        settings=settings,
        session=session,
    )
    captured = capsys.readouterr()
    output = json.loads(captured.out)

    assert exit_code == 0
    assert output["status"] == "ok"
    assert output["materialized"] == 1
    serialized = f"{captured.out}\n{captured.err}".casefold()
    assert "private customer conversation" not in serialized
    assert "database_url" not in serialized
    assert "authorization" not in serialized
    assert "token" not in serialized


def test_python_module_cli_is_production_reachable():
    result = subprocess.run(
        [sys.executable, "-m", "app.run_due_followups", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--limit" in result.stdout
    assert "--workspace-id" in result.stdout
