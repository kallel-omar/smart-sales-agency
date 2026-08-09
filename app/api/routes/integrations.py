from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import (
    CurrentWorkspaceDep,
    SessionDep,
    SettingsDep,
    VerifiedIntegrationContextDep,
)
from app.models import (
    IntegrationAccount,
    IntegrationAccountAuditAction,
    OutboundIntegrationAction,
    OutboundIntegrationDeliveryAttempt,
    utc_now,
)
from app.schemas import (
    InboundIntegrationEvent,
    IntegrationAccountAuditEventRead,
    IntegrationAccountAuditRetentionCleanupRead,
    IntegrationAccountCredentialRead,
    IntegrationAccountProvision,
    IntegrationAccountRead,
    IntegrationAccountSecretReferenceUpdate,
    OutboundIntegrationActionCreate,
    OutboundIntegrationActionRead,
    OutboundIntegrationDeliveryAttemptRead,
    SalesReply,
)
from app.services.inbound_integrations import InboundIntegrationService
from app.services.integration_account_audit import (
    DEFAULT_AUDIT_EVENT_LIMIT,
    MAX_AUDIT_EVENT_LIMIT,
    AuditQueryValidationError,
    IntegrationAccountAuditRetentionPolicy,
    IntegrationAccountAuditService,
)
from app.services.integration_accounts import (
    IntegrationAccountNotFoundError,
    IntegrationAccountService,
)
from app.services.outbound_delivery import (
    OutboundIntegrationActionAlreadyProcessedError,
    OutboundIntegrationActionNotFoundError,
    OutboundIntegrationActionNotRetryableError,
    OutboundIntegrationDeliveryService,
)
from app.services.outbound_integrations import (
    InactiveIntegrationAccountError,
    OutboundIntegrationActionIdempotencyConflictError,
    OutboundIntegrationService,
)
from app.services.repository import NotFoundError
from app.services.secret_reference_policy import SecretReferenceValidationError

router = APIRouter(prefix="/integrations", tags=["integrations"])

AuditLimit = Annotated[
    int,
    Query(
        ge=1,
        le=MAX_AUDIT_EVENT_LIMIT,
        description="Maximum number of safe audit events to return.",
    ),
]


def account_read(account: IntegrationAccount) -> IntegrationAccountRead:
    return IntegrationAccountRead.model_validate(account)


def account_credential_read(
    account: IntegrationAccount,
    credential: str,
) -> IntegrationAccountCredentialRead:
    return IntegrationAccountCredentialRead(
        **account_read(account).model_dump(),
        inbound_credential=credential,
    )


def outbound_action_read(
    action: OutboundIntegrationAction,
    account: IntegrationAccount,
) -> OutboundIntegrationActionRead:
    return OutboundIntegrationActionRead(
        id=action.id,
        workspace_id=action.workspace_id,
        integration_account_id=action.integration_account_id,
        provider=account.provider,
        external_target_id=action.external_target_id,
        action_type=action.action_type,
        content=action.content,
        correlation_id=action.correlation_id,
        status=action.status,
        provider_delivery_id=action.provider_delivery_id,
        delivered_at=action.delivered_at,
        failed_at=action.failed_at,
        failure_code=action.failure_code,
        failure_message=action.failure_message,
        created_at=action.created_at,
    )


def outbound_delivery_attempt_read(
    attempt: OutboundIntegrationDeliveryAttempt,
) -> OutboundIntegrationDeliveryAttemptRead:
    return OutboundIntegrationDeliveryAttemptRead.model_validate(attempt)


@router.post(
    "/accounts",
    response_model=IntegrationAccountCredentialRead,
    status_code=status.HTTP_201_CREATED,
)
def provision_integration_account(
    payload: IntegrationAccountProvision,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> IntegrationAccountCredentialRead:
    """Provision a workspace-owned account and return its credential once."""
    try:
        account, credential = IntegrationAccountService(session).provision(
            workspace,
            payload.provider,
            payload.external_account_id,
            payload.secret_reference,
        )
    except SecretReferenceValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Secret reference is not allowed",
        ) from exc
    return account_credential_read(account, credential)


@router.get("/accounts", response_model=list[IntegrationAccountRead])
def list_integration_accounts(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> list[IntegrationAccountRead]:
    accounts = IntegrationAccountService(session).list_for_workspace(workspace)
    return [account_read(account) for account in accounts]


@router.get(
    "/accounts/{account_id}/audit-events",
    response_model=list[IntegrationAccountAuditEventRead],
)
def list_integration_account_audit_events(
    account_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    action: IntegrationAccountAuditAction | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: AuditLimit = DEFAULT_AUDIT_EVENT_LIMIT,
) -> list[IntegrationAccountAuditEventRead]:
    """Return safe lifecycle history for an account in the current workspace."""
    account_service = IntegrationAccountService(session)
    try:
        account_service.get_for_workspace(workspace, account_id)
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc

    try:
        events = IntegrationAccountAuditService(session).list_for_account(
            workspace,
            account_id,
            action=action,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
        )
    except AuditQueryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [IntegrationAccountAuditEventRead.model_validate(event) for event in events]


@router.get(
    "/audit-events",
    response_model=list[IntegrationAccountAuditEventRead],
)
def list_workspace_integration_account_audit_events(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    action: IntegrationAccountAuditAction | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: AuditLimit = DEFAULT_AUDIT_EVENT_LIMIT,
) -> list[IntegrationAccountAuditEventRead]:
    """Return safe lifecycle history for all accounts in the current workspace."""
    try:
        events = IntegrationAccountAuditService(session).list_for_workspace(
            workspace,
            action=action,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
        )
    except AuditQueryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [IntegrationAccountAuditEventRead.model_validate(event) for event in events]


@router.post(
    "/audit-events/retention-cleanup",
    response_model=IntegrationAccountAuditRetentionCleanupRead,
)
def cleanup_workspace_integration_account_audit_events(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    settings: SettingsDep,
) -> IntegrationAccountAuditRetentionCleanupRead:
    """Explicitly remove expired audit events for the current workspace only."""
    policy = IntegrationAccountAuditRetentionPolicy(
        settings.integration_account_audit_retention_days
    )
    result = IntegrationAccountAuditService(session).cleanup_for_workspace(
        workspace,
        policy,
        now=utc_now(),
    )
    return IntegrationAccountAuditRetentionCleanupRead(
        deleted_count=result.deleted_count,
        cutoff=result.cutoff,
    )


@router.post("/accounts/{account_id}/deactivate", response_model=IntegrationAccountRead)
def deactivate_integration_account(
    account_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> IntegrationAccountRead:
    try:
        account = IntegrationAccountService(session).deactivate(workspace, account_id)
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    return account_read(account)


@router.post("/accounts/{account_id}/reactivate", response_model=IntegrationAccountRead)
def reactivate_integration_account(
    account_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> IntegrationAccountRead:
    try:
        account = IntegrationAccountService(session).reactivate(workspace, account_id)
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    return account_read(account)


@router.post(
    "/accounts/{account_id}/credential/rotate",
    response_model=IntegrationAccountCredentialRead,
)
def rotate_integration_account_credential(
    account_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> IntegrationAccountCredentialRead:
    try:
        account, credential = IntegrationAccountService(session).rotate_credential(
            workspace,
            account_id,
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    return account_credential_read(account, credential)


@router.post(
    "/accounts/{account_id}/secret-reference",
    response_model=IntegrationAccountRead,
)
def update_integration_account_secret_reference(
    account_id: UUID,
    payload: IntegrationAccountSecretReferenceUpdate,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> IntegrationAccountRead:
    """Update an account's internal secret reference without resolving it."""
    try:
        account = IntegrationAccountService(session).update_secret_reference(
            workspace,
            account_id,
            payload.secret_reference,
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    except SecretReferenceValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Secret reference is not allowed",
        ) from exc
    return account_read(account)


@router.post(
    "/accounts/{account_id}/outbound-actions",
    response_model=OutboundIntegrationActionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_outbound_integration_action(
    account_id: UUID,
    payload: OutboundIntegrationActionCreate,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> OutboundIntegrationActionRead:
    """Persist a delivery intent for a future provider adapter; do not send it."""
    try:
        action, account = OutboundIntegrationService(session).create_action(
            workspace,
            account_id,
            external_target_id=payload.external_target_id,
            action_type=payload.action_type,
            content=payload.content,
            payload=payload.payload,
            correlation_id=payload.correlation_id,
            idempotency_key=payload.idempotency_key,
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    except InactiveIntegrationAccountError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OutboundIntegrationActionIdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return outbound_action_read(action, account)


@router.post(
    "/accounts/{account_id}/outbound-actions/{action_id}/deliver",
    response_model=OutboundIntegrationActionRead,
)
def deliver_outbound_integration_action(
    account_id: UUID,
    action_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> OutboundIntegrationActionRead:
    """Explicitly process one pending action through a neutral adapter."""
    try:
        action, account = OutboundIntegrationDeliveryService(session).deliver_pending_action(
            workspace,
            account_id,
            action_id,
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    except OutboundIntegrationActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Outbound integration action not found") from exc
    except InactiveIntegrationAccountError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OutboundIntegrationActionAlreadyProcessedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return outbound_action_read(action, account)


@router.post(
    "/accounts/{account_id}/outbound-actions/{action_id}/retry",
    response_model=OutboundIntegrationActionRead,
)
def retry_failed_outbound_integration_action(
    account_id: UUID,
    action_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> OutboundIntegrationActionRead:
    """Explicitly retry one failed action with the same persisted identity."""
    try:
        action, account = OutboundIntegrationDeliveryService(session).retry_failed_action(
            workspace,
            account_id,
            action_id,
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    except OutboundIntegrationActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Outbound integration action not found") from exc
    except InactiveIntegrationAccountError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OutboundIntegrationActionNotRetryableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return outbound_action_read(action, account)


@router.get(
    "/accounts/{account_id}/outbound-actions/{action_id}/delivery-attempts",
    response_model=list[OutboundIntegrationDeliveryAttemptRead],
)
def list_outbound_integration_delivery_attempts(
    account_id: UUID,
    action_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> list[OutboundIntegrationDeliveryAttemptRead]:
    """Return safe, ordered delivery-attempt history for one scoped action."""
    try:
        attempts = OutboundIntegrationDeliveryService(session).list_attempts_for_action(
            workspace,
            account_id,
            action_id,
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    except OutboundIntegrationActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Outbound integration action not found") from exc
    return [outbound_delivery_attempt_read(attempt) for attempt in attempts]


@router.post("/inbound-events", response_model=SalesReply)
async def receive_inbound_event(
    payload: InboundIntegrationEvent,
    session: SessionDep,
    integration_context: VerifiedIntegrationContextDep,
    settings: SettingsDep,
) -> SalesReply:
    """Accept a normalized provider-neutral inbound integration event."""

    integration_service = InboundIntegrationService(session, settings)

    try:
        result = await integration_service.handle_event(
            payload,
            integration_context.workspace,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lead not found") from exc

    return SalesReply(
        lead_id=payload.lead_id,
        detected_stage=result.detected_stage,
        draft_reply=result.draft_reply,
        approval_id=result.approval_id,
    )
