from datetime import datetime
from typing import Annotated, Literal
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
    OutboundIntegrationActionStatus,
    OutboundIntegrationAuditAction,
    OutboundIntegrationDeliveryAttempt,
    utc_now,
)
from app.schemas import (
    InboundIntegrationEvent,
    IntegrationAccountAuditEventRead,
    IntegrationAccountAuditRetentionCleanupRead,
    IntegrationAccountCredentialRead,
    IntegrationAccountHealthRead,
    IntegrationAccountProvision,
    IntegrationAccountRead,
    IntegrationAccountSecretReferenceUpdate,
    OutboundActionExpirationCleanupRead,
    OutboundIntegrationActionCreate,
    OutboundIntegrationActionDetailRead,
    OutboundIntegrationActionRead,
    OutboundIntegrationActionSummaryRead,
    OutboundIntegrationAuditEventRead,
    OutboundIntegrationDeliveryAttemptRead,
    OutboundIntegrationDeliveryStatusRead,
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
from app.services.integration_health import IntegrationHealthService
from app.services.outbound_action_audit import (
    DEFAULT_OUTBOUND_AUDIT_EVENT_LIMIT,
    MAX_OUTBOUND_AUDIT_EVENT_LIMIT,
    OutboundAuditQueryValidationError,
    OutboundIntegrationActionAuditService,
)
from app.services.outbound_action_query import (
    DEFAULT_OUTBOUND_ACTION_LIMIT,
    MAX_OUTBOUND_ACTION_LIMIT,
    OutboundActionQueryValidationError,
    OutboundIntegrationActionQueryNotFoundError,
    OutboundIntegrationActionQueryService,
)
from app.services.outbound_delivery import (
    DEFAULT_DELIVERY_ATTEMPT_LIMIT,
    MAX_DELIVERY_ATTEMPT_LIMIT,
    OutboundDeliveryAttemptQueryValidationError,
    OutboundIntegrationActionAlreadyProcessedError,
    OutboundIntegrationActionExpiredError,
    OutboundIntegrationActionNotCancellableError,
    OutboundIntegrationActionNotFoundError,
    OutboundIntegrationActionNotRetryableError,
    OutboundIntegrationActionRetryDeniedError,
    OutboundIntegrationDeliveryService,
)
from app.services.outbound_delivery_status import (
    OutboundIntegrationDeliveryStatusService,
    OutboundIntegrationDeliveryStatusView,
)
from app.services.outbound_integrations import (
    InactiveIntegrationAccountError,
    OutboundIntegrationActionIdempotencyConflictError,
    OutboundIntegrationService,
)
from app.services.outbound_retry_delay_policy import OutboundDeliveryRetryDelayPolicy
from app.services.outbound_retry_policy import OutboundDeliveryRetryPolicy
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

OutboundActionLimit = Annotated[
    int,
    Query(
        ge=1,
        le=MAX_OUTBOUND_ACTION_LIMIT,
        description="Maximum number of safe outbound action summaries to return.",
    ),
]

OutboundActionStatusFilter = Annotated[
    OutboundIntegrationActionStatus | None,
    Query(alias="status"),
]

DeliveryAttemptLimit = Annotated[
    int,
    Query(
        ge=1,
        le=MAX_DELIVERY_ATTEMPT_LIMIT,
        description="Maximum number of safe delivery attempts to return.",
    ),
]

OutboundAuditLimit = Annotated[
    int,
    Query(
        ge=1,
        le=MAX_OUTBOUND_AUDIT_EVENT_LIMIT,
        description="Maximum number of safe outbound audit events to return.",
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
        cancelled_at=action.cancelled_at,
        expires_at=action.expires_at,
        expired_at=action.expired_at,
        failure_code=action.failure_code,
        failure_message=action.failure_message,
        created_at=action.created_at,
    )


def outbound_action_summary_read(
    action: OutboundIntegrationAction,
    provider: str,
) -> OutboundIntegrationActionSummaryRead:
    return OutboundIntegrationActionSummaryRead(
        id=action.id,
        integration_account_id=action.integration_account_id,
        provider=provider,
        external_target_id=action.external_target_id,
        action_type=action.action_type,
        status=action.status,
        provider_delivery_id=action.provider_delivery_id,
        delivered_at=action.delivered_at,
        failed_at=action.failed_at,
        cancelled_at=action.cancelled_at,
        expires_at=action.expires_at,
        expired_at=action.expired_at,
        failure_code=action.failure_code,
        created_at=action.created_at,
    )


def outbound_action_detail_read(
    action: OutboundIntegrationAction,
    provider: str,
) -> OutboundIntegrationActionDetailRead:
    return OutboundIntegrationActionDetailRead(
        **outbound_action_summary_read(action, provider).model_dump(),
        failure_message=action.failure_message,
    )


def outbound_delivery_attempt_read(
    attempt: OutboundIntegrationDeliveryAttempt,
) -> OutboundIntegrationDeliveryAttemptRead:
    return OutboundIntegrationDeliveryAttemptRead.model_validate(attempt)


def outbound_delivery_status_read(
    view: OutboundIntegrationDeliveryStatusView,
) -> OutboundIntegrationDeliveryStatusRead:
    action = view.action
    return OutboundIntegrationDeliveryStatusRead(
        id=action.id,
        provider=view.account.provider,
        external_target_id=action.external_target_id,
        action_type=action.action_type,
        status=action.status,
        created_at=action.created_at,
        provider_delivery_id=action.provider_delivery_id,
        delivered_at=action.delivered_at,
        failed_at=action.failed_at,
        failure_code=action.failure_code,
        failure_message=action.failure_message,
        attempt_count=view.attempt_count,
        retry_allowed=view.retry_eligibility.allowed,
        retry_denial_reason=view.retry_eligibility.denial_reason,
        next_retry_at=view.next_retry_at,
    )


@router.get(
    "/outbound-audit-events",
    response_model=list[OutboundIntegrationAuditEventRead],
)
def list_outbound_integration_audit_events(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    action: OutboundIntegrationAuditAction | None = None,
    integration_account_id: UUID | None = None,
    outbound_integration_action_id: UUID | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: OutboundAuditLimit = DEFAULT_OUTBOUND_AUDIT_EVENT_LIMIT,
) -> list[OutboundIntegrationAuditEventRead]:
    """List safe outbound lifecycle history from the current workspace only."""
    try:
        events = OutboundIntegrationActionAuditService(session).list_for_workspace(
            workspace,
            action=action,
            integration_account_id=integration_account_id,
            outbound_integration_action_id=outbound_integration_action_id,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
        )
    except OutboundAuditQueryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [OutboundIntegrationAuditEventRead.model_validate(event) for event in events]


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


@router.get("/accounts/{account_id}/health", response_model=IntegrationAccountHealthRead)
def get_integration_account_health(
    account_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    settings: SettingsDep,
) -> IntegrationAccountHealthRead:
    """Read persisted operational health without contacting an external provider."""
    try:
        view = IntegrationHealthService(session).get_for_account(
            workspace,
            account_id,
            window_days=settings.integration_health_window_days,
            now=utc_now(),
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    return IntegrationAccountHealthRead(
        id=view.account.id,
        provider=view.account.provider,
        active=view.account.active,
        health=view.health,
        most_recent_outbound_at=view.most_recent_outbound_at,
        recent_delivered_count=view.recent_delivered_count,
        recent_failed_count=view.recent_failed_count,
        pending_action_count=view.pending_action_count,
        failed_action_count=view.failed_action_count,
    )


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
        expires_at=payload.expires_at,
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    except InactiveIntegrationAccountError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OutboundIntegrationActionIdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return outbound_action_read(action, account)


@router.get(
    "/outbound-actions",
    response_model=list[OutboundIntegrationActionSummaryRead],
)
def list_outbound_integration_actions(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    action_status: OutboundActionStatusFilter = None,
    provider: str | None = Query(default=None, min_length=1, max_length=100),
    integration_account_id: UUID | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: OutboundActionLimit = DEFAULT_OUTBOUND_ACTION_LIMIT,
) -> list[OutboundIntegrationActionSummaryRead]:
    """List safe outbound action summaries from the current workspace only."""
    try:
        rows = OutboundIntegrationActionQueryService(session).list_for_workspace(
            workspace,
            action_status=action_status,
            provider=provider,
            integration_account_id=integration_account_id,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
        )
    except OutboundActionQueryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [outbound_action_summary_read(action, provider_name) for action, provider_name in rows]


@router.post("/outbound-actions/expiration-cleanup", response_model=OutboundActionExpirationCleanupRead)
def cleanup_expired_outbound_actions(
    session: SessionDep, workspace: CurrentWorkspaceDep
) -> OutboundActionExpirationCleanupRead:
    cutoff = utc_now()
    deleted_count = OutboundIntegrationActionQueryService(session).cleanup_expired_for_workspace(
        workspace, cutoff
    )
    return OutboundActionExpirationCleanupRead(deleted_count=deleted_count, cutoff=cutoff)


@router.get(
    "/outbound-actions/{action_id}",
    response_model=OutboundIntegrationActionDetailRead,
)
def get_outbound_integration_action_detail(
    action_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> OutboundIntegrationActionDetailRead:
    """Read one safe outbound action view without delivering or mutating it."""
    try:
        action, provider = OutboundIntegrationActionQueryService(session).get_for_workspace(
            workspace,
            action_id,
        )
    except OutboundIntegrationActionQueryNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Outbound integration action not found") from exc
    return outbound_action_detail_read(action, provider)


@router.post(
    "/accounts/{account_id}/outbound-actions/{action_id}/deliver",
    response_model=OutboundIntegrationActionRead,
)
def deliver_outbound_integration_action(
    account_id: UUID,
    action_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    settings: SettingsDep,
) -> OutboundIntegrationActionRead:
    """Explicitly process one pending action through a neutral adapter."""
    try:
        action, account = OutboundIntegrationDeliveryService.from_settings(session, settings).deliver_pending_action(
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
    except OutboundIntegrationActionExpiredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return outbound_action_read(action, account)


@router.post(
    "/accounts/{account_id}/outbound-actions/{action_id}/cancel",
    response_model=OutboundIntegrationActionRead,
)
def cancel_outbound_integration_action(
    account_id: UUID, action_id: UUID, session: SessionDep, workspace: CurrentWorkspaceDep
) -> OutboundIntegrationActionRead:
    try:
        action, account = OutboundIntegrationDeliveryService(session).cancel_pending_action(
            workspace, account_id, action_id
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    except OutboundIntegrationActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Outbound integration action not found") from exc
    except OutboundIntegrationActionNotCancellableError as exc:
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
    settings: SettingsDep,
) -> OutboundIntegrationActionRead:
    """Explicitly retry one failed action with the same persisted identity."""
    try:
        action, account = OutboundIntegrationDeliveryService.from_settings(
            session,
            settings,
            retry_policy=OutboundDeliveryRetryPolicy.from_settings(settings),
        ).retry_failed_action(
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
    except OutboundIntegrationActionRetryDeniedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OutboundIntegrationActionNotRetryableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return outbound_action_read(action, account)


@router.get(
    "/accounts/{account_id}/outbound-actions/{action_id}/delivery-status",
    response_model=OutboundIntegrationDeliveryStatusRead,
)
def get_outbound_integration_delivery_status(
    account_id: UUID,
    action_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    settings: SettingsDep,
) -> OutboundIntegrationDeliveryStatusRead:
    """Return one safe status summary without delivering or retrying the action."""
    try:
        view = OutboundIntegrationDeliveryStatusService(
            session,
            retry_policy=OutboundDeliveryRetryPolicy.from_settings(settings),
            retry_delay_policy=OutboundDeliveryRetryDelayPolicy.from_settings(settings),
        ).get_status_for_action(
            workspace,
            account_id,
            action_id,
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    except OutboundIntegrationActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Outbound integration action not found") from exc
    return outbound_delivery_status_read(view)


@router.get(
    "/accounts/{account_id}/outbound-actions/{action_id}/delivery-attempts",
    response_model=list[OutboundIntegrationDeliveryAttemptRead],
)
def list_outbound_integration_delivery_attempts(
    account_id: UUID,
    action_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    attempt_status: OutboundActionStatusFilter = None,
    started_after: datetime | None = None,
    started_before: datetime | None = None,
    order: Literal["oldest_first", "newest_first"] = "oldest_first",
    limit: DeliveryAttemptLimit = DEFAULT_DELIVERY_ATTEMPT_LIMIT,
) -> list[OutboundIntegrationDeliveryAttemptRead]:
    """Return safe, ordered delivery-attempt history for one scoped action."""
    try:
        attempts = OutboundIntegrationDeliveryService(session).list_attempts_for_action(
            workspace,
            account_id,
            action_id,
            attempt_status=attempt_status,
            started_after=started_after,
            started_before=started_before,
            newest_first=order == "newest_first",
            limit=limit,
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    except OutboundIntegrationActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Outbound integration action not found") from exc
    except OutboundDeliveryAttemptQueryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
