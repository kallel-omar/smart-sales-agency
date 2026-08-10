"""Durable retry protection for direct Sales conversation API turns."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from app.config import Settings
from app.departments.sales.services.conversation_turn_service import (
    SalesConversationTurnInput,
    SalesConversationTurnResult,
    SalesConversationTurnService,
)
from app.models import (
    DirectConversationTurnReceipt,
    DirectConversationTurnReceiptStatus,
    Workspace,
)
from app.services.ai_invocation_gateway import AIInvocationGateway
from app.services.repository import NotFoundError, SalesRepository


class DirectConversationTurnIdempotencyValidationError(ValueError):
    """Raised when a direct client retry key is missing or unsafe."""


class DirectConversationTurnIdempotencyConflictError(RuntimeError):
    """Raised when a key is reused for another turn or remains in progress."""


@dataclass(frozen=True, slots=True)
class DirectSalesConversationTurnOutcome:
    """Safe direct-turn result, including whether it came from a receipt."""

    turn_result: SalesConversationTurnResult
    duplicate: bool = False


class DirectSalesConversationTurnService:
    """Coordinate direct API idempotency around the Task 273 turn boundary.

    Integration webhooks retain their own Task 251 receipt contract. This
    service owns only an optional client-provided direct-turn key.
    """

    _KEY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}")

    def __init__(
        self,
        *,
        repository: SalesRepository,
        settings: Settings,
        workspace: Workspace,
        ai_invocation_gateway: AIInvocationGateway | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.workspace = workspace
        self.ai_invocation_gateway = ai_invocation_gateway

    async def process(
        self,
        source: SalesConversationTurnInput,
        *,
        idempotency_key: str | None,
    ) -> DirectSalesConversationTurnOutcome:
        """Run a direct turn once, or return its safely persisted outcome."""

        if idempotency_key is None:
            return DirectSalesConversationTurnOutcome(
                turn_result=await self._process_turn(source),
            )

        normalized_key = self.normalize_key(idempotency_key)
        fingerprint = self.request_fingerprint(source)
        lead = self.repository.get_lead(source.lead_id)
        if lead.tenant_id != self.workspace.slug:
            raise NotFoundError("Lead not found")

        receipt, reserved = self.repository.reserve_direct_conversation_turn_receipt(
            workspace=self.workspace,
            lead=lead,
            idempotency_key=normalized_key,
            request_fingerprint=fingerprint,
        )
        if not reserved:
            return self._replay_or_raise(receipt, fingerprint)

        try:
            result = await self._process_turn(source)
        except Exception:
            # A provider/application failure has no completed outcome. Releasing
            # the reservation permits an explicit client retry with this key;
            # it never fabricates a completed turn or retries AI automatically.
            self.repository.discard_direct_conversation_turn_receipt(receipt)
            raise

        self.repository.complete_direct_conversation_turn_receipt(
            receipt,
            detected_stage=result.detected_stage,
            draft_reply=result.draft_reply,
            approval_id=result.approval_id,
            handoff_required=result.handoff_required,
            handoff_reason_code=result.handoff_reason_code,
        )
        return DirectSalesConversationTurnOutcome(turn_result=result)

    async def _process_turn(
        self,
        source: SalesConversationTurnInput,
    ) -> SalesConversationTurnResult:
        return await SalesConversationTurnService(
            repository=self.repository,
            settings=self.settings,
            workspace=self.workspace,
            ai_invocation_gateway=self.ai_invocation_gateway,
        ).process(source)

    @classmethod
    def normalize_key(cls, value: str) -> str:
        normalized = value.strip()
        if not cls._KEY_PATTERN.fullmatch(normalized):
            raise DirectConversationTurnIdempotencyValidationError(
                "Idempotency-Key must be 1-200 characters using letters, numbers, '.', '_', ':', or '-'"
            )
        return normalized

    @staticmethod
    def request_fingerprint(source: SalesConversationTurnInput) -> str:
        """Hash only normalized direct-turn semantics with stable serialization."""

        payload = json.dumps(
            {
                "channel": source.channel,
                "customer_message": source.customer_message,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _replay_or_raise(
        receipt: DirectConversationTurnReceipt,
        fingerprint: str,
    ) -> DirectSalesConversationTurnOutcome:
        if receipt.request_fingerprint != fingerprint:
            raise DirectConversationTurnIdempotencyConflictError(
                "Idempotency-Key has already been used for a different conversation turn"
            )
        if receipt.status == DirectConversationTurnReceiptStatus.IN_PROGRESS:
            raise DirectConversationTurnIdempotencyConflictError(
                "A conversation turn with this Idempotency-Key is already in progress"
            )
        if (
            receipt.status != DirectConversationTurnReceiptStatus.COMPLETED
            or receipt.detected_stage is None
            or receipt.draft_reply is None
        ):
            raise RuntimeError("Direct conversation turn receipt is incomplete")
        return DirectSalesConversationTurnOutcome(
            turn_result=SalesConversationTurnResult(
                lead_id=receipt.lead_id,
                detected_stage=receipt.detected_stage,
                draft_reply=receipt.draft_reply,
                approval_id=receipt.approval_id,
                handoff_required=receipt.handoff_required,
                handoff_reason_code=receipt.handoff_reason_code,
                ai_invoked=False,
            ),
            duplicate=True,
        )
