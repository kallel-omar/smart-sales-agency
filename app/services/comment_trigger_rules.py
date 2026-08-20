from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.capabilities import BusinessCapabilityKey
from app.core.comment_triggers import InboundCommentChannel
from app.core.events import Department as DepartmentKind
from app.integrations.providers import (
    FACEBOOK_MESSENGER_PROVIDER,
    INSTAGRAM_DM_PROVIDER,
)
from app.models import (
    AIEmployee,
    AIEmployeeCapabilityAssignment,
    Capability,
    Department,
    InboundCommentTriggerRule,
    IntegrationAccount,
    Workspace,
    utc_now,
)


class CommentTriggerRuleNotFoundError(LookupError):
    pass


class CommentTriggerRuleValidationError(ValueError):
    pass


class DuplicateCommentTriggerRuleError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CommentTriggerMatch:
    rule: InboundCommentTriggerRule | None
    ambiguous: bool = False


class CommentTriggerRuleService:
    _PROVIDER_BY_CHANNEL: ClassVar[dict[InboundCommentChannel, str]] = {
        InboundCommentChannel.FACEBOOK_COMMENT: FACEBOOK_MESSENGER_PROVIDER,
        InboundCommentChannel.INSTAGRAM_COMMENT: INSTAGRAM_DM_PROVIDER,
    }

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        workspace: Workspace,
        *,
        integration_account_id: UUID,
        channel: InboundCommentChannel,
        name: str,
        enabled: bool,
        keywords: list[str],
        content_external_id: str | None,
        dm_message: str,
        send_assignment_id: UUID,
    ) -> InboundCommentTriggerRule:
        canonical_channel = InboundCommentChannel(channel)
        self._account(workspace, integration_account_id, canonical_channel)
        self._send_assignment(workspace, send_assignment_id)
        rule = InboundCommentTriggerRule(
            workspace_id=workspace.id,
            integration_account_id=integration_account_id,
            channel=canonical_channel,
            name=self._text(name, "Rule name", 200),
            enabled=bool(enabled),
            keywords=self._keywords(keywords),
            content_external_id=self._optional_text(content_external_id, 255),
            dm_message=self._text(dm_message, "DM message", 10_000),
            send_assignment_id=send_assignment_id,
        )
        self.session.add(rule)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise DuplicateCommentTriggerRuleError(
                "A comment trigger rule with this name already exists"
            ) from exc
        self.session.refresh(rule)
        return rule

    def list_for_workspace(self, workspace: Workspace) -> list[InboundCommentTriggerRule]:
        return list(
            self.session.exec(
                select(InboundCommentTriggerRule)
                .where(InboundCommentTriggerRule.workspace_id == workspace.id)
                .order_by(
                    InboundCommentTriggerRule.created_at.asc(),
                    InboundCommentTriggerRule.id.asc(),
                )
            ).all()
        )

    def get(self, workspace: Workspace, rule_id: UUID) -> InboundCommentTriggerRule:
        rule = self.session.exec(
            select(InboundCommentTriggerRule).where(
                InboundCommentTriggerRule.id == rule_id,
                InboundCommentTriggerRule.workspace_id == workspace.id,
            )
        ).first()
        if rule is None:
            raise CommentTriggerRuleNotFoundError("Comment trigger rule not found")
        return rule

    def update(
        self,
        workspace: Workspace,
        rule_id: UUID,
        *,
        values: dict,
    ) -> InboundCommentTriggerRule:
        rule = self.get(workspace, rule_id)
        if values.get("name") is not None:
            rule.name = self._text(values["name"], "Rule name", 200)
        if values.get("keywords") is not None:
            rule.keywords = self._keywords(values["keywords"])
        if values.get("dm_message") is not None:
            rule.dm_message = self._text(values["dm_message"], "DM message", 10_000)
        if values.get("send_assignment_id") is not None:
            self._send_assignment(workspace, values["send_assignment_id"])
            rule.send_assignment_id = values["send_assignment_id"]
        if values.get("clear_content_external_id"):
            rule.content_external_id = None
        elif "content_external_id" in values and values["content_external_id"] is not None:
            rule.content_external_id = self._optional_text(values["content_external_id"], 255)
        rule.updated_at = utc_now()
        self.session.add(rule)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise DuplicateCommentTriggerRuleError(
                "A comment trigger rule with this name already exists"
            ) from exc
        self.session.refresh(rule)
        return rule

    def set_enabled(
        self, workspace: Workspace, rule_id: UUID, enabled: bool
    ) -> InboundCommentTriggerRule:
        rule = self.get(workspace, rule_id)
        rule.enabled = enabled
        rule.updated_at = utc_now()
        self.session.add(rule)
        self.session.commit()
        self.session.refresh(rule)
        return rule

    def match(
        self,
        workspace: Workspace,
        account: IntegrationAccount,
        channel: InboundCommentChannel,
        content: str,
        content_external_id: str | None,
    ) -> CommentTriggerMatch:
        canonical_channel = InboundCommentChannel(channel)
        self._account(workspace, account.id, canonical_channel)
        normalized_content = content.strip().casefold()
        candidates = self.session.exec(
            select(InboundCommentTriggerRule).where(
                InboundCommentTriggerRule.workspace_id == workspace.id,
                InboundCommentTriggerRule.integration_account_id == account.id,
                InboundCommentTriggerRule.channel == canonical_channel,
                InboundCommentTriggerRule.enabled.is_(True),
            )
        ).all()
        matches = [
            rule
            for rule in candidates
            if (rule.content_external_id is None or rule.content_external_id == content_external_id)
            and any(keyword.casefold() in normalized_content for keyword in rule.keywords)
        ]
        if len(matches) > 1:
            return CommentTriggerMatch(rule=None, ambiguous=True)
        return CommentTriggerMatch(rule=matches[0] if matches else None)

    def resolve_send_context(
        self,
        workspace: Workspace,
        rule: InboundCommentTriggerRule,
    ) -> tuple[AIEmployeeCapabilityAssignment, AIEmployee, Capability, Department]:
        if rule.workspace_id != workspace.id:
            raise CommentTriggerRuleNotFoundError("Comment trigger rule not found")
        return self._send_assignment(workspace, rule.send_assignment_id)

    def _account(
        self,
        workspace: Workspace,
        account_id: UUID,
        channel: InboundCommentChannel,
    ) -> IntegrationAccount:
        account = self.session.exec(
            select(IntegrationAccount).where(
                IntegrationAccount.id == account_id,
                IntegrationAccount.workspace_id == workspace.id,
            )
        ).first()
        if account is None:
            raise CommentTriggerRuleValidationError("Integration account not found")
        if account.provider != self._PROVIDER_BY_CHANNEL[channel]:
            raise CommentTriggerRuleValidationError(
                "Comment channel is incompatible with the IntegrationAccount"
            )
        return account

    def _send_assignment(
        self,
        workspace: Workspace,
        assignment_id: UUID,
    ) -> tuple[AIEmployeeCapabilityAssignment, AIEmployee, Capability, Department]:
        assignment = self.session.exec(
            select(AIEmployeeCapabilityAssignment).where(
                AIEmployeeCapabilityAssignment.id == assignment_id,
                AIEmployeeCapabilityAssignment.workspace_id == workspace.id,
            )
        ).first()
        if assignment is None:
            raise CommentTriggerRuleValidationError("send_message assignment not found")
        employee = self.session.get(AIEmployee, assignment.ai_employee_id)
        capability = self.session.get(Capability, assignment.capability_id)
        if employee is None or capability is None:
            raise CommentTriggerRuleValidationError("send_message assignment is incomplete")
        department = self.session.get(Department, employee.department_id)
        if (
            employee.workspace_id != workspace.id
            or capability.workspace_id != workspace.id
            or department is None
            or department.workspace_id != workspace.id
            or employee.department_id != capability.department_id
            or capability.department_id != department.id
            or department.kind != DepartmentKind.SALES
            or capability.key != BusinessCapabilityKey.SEND_MESSAGE
        ):
            raise CommentTriggerRuleValidationError(
                "Assignment must provide send_message in the Sales Department"
            )
        return assignment, employee, capability, department

    @staticmethod
    def _keywords(values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = CommentTriggerRuleService._text(value, "Keyword", 200)
            canonical = item.casefold()
            if canonical not in seen:
                seen.add(canonical)
                normalized.append(item)
        if not normalized:
            raise CommentTriggerRuleValidationError("At least one keyword or phrase is required")
        return normalized

    @staticmethod
    def _text(value: str, label: str, maximum: int) -> str:
        normalized = value.strip()
        if not normalized:
            raise CommentTriggerRuleValidationError(f"{label} is required")
        if len(normalized) > maximum:
            raise CommentTriggerRuleValidationError(f"{label} is too long")
        return normalized

    @staticmethod
    def _optional_text(value: str | None, maximum: int) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if len(normalized) > maximum:
            raise CommentTriggerRuleValidationError("Content identifier is too long")
        return normalized
