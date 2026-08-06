from datetime import datetime, timedelta, timezone

from sqlmodel import Session

from app.models import FollowUpTask, Lead


class FollowUpAgent:
    def __init__(self, session: Session):
        self.session = session

    def schedule(self, lead: Lead, reason: str, delay_days: int = 2) -> FollowUpTask:
        task = FollowUpTask(
            lead_id=lead.id,
            due_at=datetime.now(timezone.utc) + timedelta(days=max(1, delay_days)),
            reason=reason,
        )
        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)
        return task
