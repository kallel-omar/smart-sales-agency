from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import Session

from app.models import FollowUpTask, Lead, LeadStatus


class FollowUpAgent:
    def __init__(self, session: Session):
        self.session = session

    def schedule(
        self,
        lead: Lead,
        reason: str,
        delay_days: int = 2,
    ) -> FollowUpTask:
        task = FollowUpTask(
            lead_id=lead.id,
            due_at=datetime.now(UTC) + timedelta(days=max(1, delay_days)),
            reason=reason,
        )

        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)

        return task

    def decide(
        self,
        task: FollowUpTask,
        lead: Lead,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve an existing scheduled follow-up without performing delivery."""

        if LeadStatus(lead.status) in {
            LeadStatus.WON,
            LeadStatus.LOST,
            LeadStatus.UNQUALIFIED,
        }:
            return {
                "action": "no_send",
                "reason": f"lead_status_{LeadStatus(lead.status).value}",
            }

        outbound_fields = (
            "message",
            "integration_account_id",
            "channel",
            "recipient",
        )
        if all(context.get(field) for field in outbound_fields):
            return {
                "action": "send",
                "reason": task.reason,
                **{field: context[field] for field in outbound_fields},
            }
        raise ValueError("Follow-up outbound context is not configured")
