from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.api.dependencies import (
    AIUsageReadPermissionDep,
    AuthenticatedWorkspaceContextDep,
    CurrentWorkspaceDep,
    IntegrationIngestRateLimitDep,
    IntegrationManagePermissionDep,
    IntegrationReadinessReadPermissionDep,
    IntegrationReadPermissionDep,
    OutboundActionOperatePermissionDep,
    OutboundDeliveryRateLimitDep,
    SessionDep,
    SettingsDep,
    VerifiedIntegrationContextDep,
    WorkspaceReadinessDep,
)
from app.models import (
    IntegrationAccount,
    IntegrationAccountAuditAction,
    OutboundActionAnnotation,
    OutboundActionLabel,
    OutboundActionPriority,
    OutboundIntegrationAction,
    OutboundIntegrationActionStatus,
    OutboundIntegrationAuditAction,
    OutboundIntegrationDeliveryAttempt,
    utc_now,
)
from app.schemas import (
    AIInvocationUsageRead,
    AIInvocationUsageSummaryRead,
    InboundIntegrationDuplicateRead,
    InboundIntegrationEvent,
    InboundIntegrationReplyRead,
    IntegrationAccountAuditEventRead,
    IntegrationAccountAuditRetentionCleanupRead,
    IntegrationAccountCommentToMessageEligibilityUpdate,
    IntegrationAccountCredentialRead,
    IntegrationAccountHealthRead,
    IntegrationAccountProvision,
    IntegrationAccountRead,
    IntegrationAccountSecretReferenceUpdate,
    IntegrationCredentialReferenceRead,
    IntegrationCredentialReferenceUpsert,
    IntegrationExecutionDeliveryAttemptRead,
    IntegrationExecutionInboundReceiptRead,
    IntegrationExecutionOutboundActionRead,
    IntegrationExecutionTraceRead,
    IntegrationOperationalSummaryRead,
    IntegrationRuntimeReadinessRead,
    OutboundActionAnnotationCreate,
    OutboundActionAnnotationRead,
    OutboundActionExpirationCleanupRead,
    OutboundActionLabelCreate,
    OutboundActionLabelRead,
    OutboundActionOwnerReferenceUpdate,
    OutboundActionPriorityUpdate,
    OutboundActionStateHistoryEntryRead,
    OutboundActionTimelineEntryRead,
    OutboundActionTransitionExplanationRead,
    OutboundActionTransitionValidationRead,
    OutboundApprovalStatusRead,
    OutboundDeliveryReadinessRead,
    OutboundIntegrationActionCreate,
    OutboundIntegrationActionDetailRead,
    OutboundIntegrationActionRead,
    OutboundIntegrationActionSummaryRead,
    OutboundIntegrationAuditEventRead,
    OutboundIntegrationDeliveryAttemptRead,
    OutboundIntegrationDeliveryStatusRead,
    ProviderDeliveryStatusEventCreate,
    ProviderDeliveryStatusEventIngestRead,
    ProviderDeliveryStatusEventRead,
    SalesReply,
)
from app.services.ai_invocation_usage import AIInvocationUsageService
from app.services.channel_connections import (
    ChannelConnectionLifecycleError,
    ChannelConnectionService,
)
from app.services.inbound_integrations import (
    InboundIntegrationEventIdValidationError,
    InboundIntegrationService,
)
from app.services.integration_account_audit import (
    DEFAULT_AUDIT_EVENT_LIMIT,
    MAX_AUDIT_EVENT_LIMIT,
    AuditQueryValidationError,
    IntegrationAccountAuditRetentionPolicy,
    IntegrationAccountAuditService,
)
from app.services.integration_accounts import (
    IntegrationAccountNotFoundError,
    IntegrationAccountOwnershipConflictError,
    IntegrationAccountProviderAuthModeError,
    IntegrationAccountProviderValidationError,
    IntegrationAccountService,
)
from app.services.integration_credential_references import (
    IntegrationCredentialPurposeValidationError,
    IntegrationCredentialReferenceService,
)
from app.services.integration_execution_trace import (
    IntegrationExecutionTraceNotFoundError,
    IntegrationExecutionTraceService,
    IntegrationExecutionTraceView,
)
from app.services.integration_health import IntegrationHealthService
from app.services.integration_operational_summary import IntegrationOperationalSummaryService
from app.services.integration_runtime_readiness import IntegrationRuntimeReadinessService
from app.services.outbound_action_annotations import OutboundActionAnnotationService
from app.services.outbound_action_archiving import (
    OutboundActionArchivingService,
    OutboundIntegrationActionAlreadyArchivedError,
    OutboundIntegrationActionNotArchivableError,
    OutboundIntegrationActionNotArchivedError,
)
from app.services.outbound_action_audit import (
    DEFAULT_OUTBOUND_AUDIT_EVENT_LIMIT,
    MAX_OUTBOUND_AUDIT_EVENT_LIMIT,
    OutboundAuditQueryValidationError,
    OutboundIntegrationActionAuditService,
)
from app.services.outbound_action_labels import (
    MAX_OUTBOUND_ACTION_LABELS,
    OutboundActionLabelNotFoundError,
    OutboundActionLabelService,
    OutboundActionLabelValidationError,
)
from app.services.outbound_action_ownership import (
    OutboundActionOwnerReferenceValidationError,
    OutboundActionOwnershipService,
)
from app.services.outbound_action_query import (
    DEFAULT_OUTBOUND_ACTION_LIMIT,
    MAX_OUTBOUND_ACTION_LIMIT,
    OutboundActionQueryValidationError,
    OutboundIntegrationActionQueryNotFoundError,
    OutboundIntegrationActionQueryService,
)
from app.services.outbound_action_state_history import (
    DEFAULT_OUTBOUND_STATE_HISTORY_LIMIT,
    MAX_OUTBOUND_STATE_HISTORY_LIMIT,
    OutboundActionStateHistoryService,
)
from app.services.outbound_action_timeline import (
    DEFAULT_OUTBOUND_ACTION_TIMELINE_LIMIT,
    MAX_OUTBOUND_ACTION_TIMELINE_LIMIT,
    OutboundActionTimelineCategory,
    OutboundActionTimelineEvent,
    OutboundActionTimelineService,
)
from app.services.outbound_action_transition_validation import (
    OutboundActionTransitionValidationService,
)
from app.services.outbound_approval_status import (
    OutboundApprovalStatusNotFoundError,
    OutboundApprovalStatusService,
)
from app.services.outbound_delivery import (
    DEFAULT_DELIVERY_ATTEMPT_LIMIT,
    MAX_DELIVERY_ATTEMPT_LIMIT,
    IntegrationAccountReconnectRequiredError,
    OutboundDeliveryAttemptQueryValidationError,
    OutboundIntegrationActionAlreadyProcessedError,
    OutboundIntegrationActionExpiredError,
    OutboundIntegrationActionNotCancellableError,
    OutboundIntegrationActionNotFoundError,
    OutboundIntegrationActionNotReadyError,
    OutboundIntegrationActionNotRetryableError,
    OutboundIntegrationActionRetryDeniedError,
    OutboundIntegrationDeliveryService,
)
from app.services.outbound_delivery_approvals import (
    OutboundDeliveryApprovalRejectedError,
    OutboundDeliveryApprovalRequiredError,
)
from app.services.outbound_delivery_readiness import OutboundDeliveryReadinessService
from app.services.outbound_delivery_status import (
    OutboundIntegrationDeliveryStatusService,
    OutboundIntegrationDeliveryStatusView,
)
from app.services.outbound_integrations import (
    InactiveIntegrationAccountError,
    OutboundIntegrationActionIdempotencyConflictError,
    OutboundIntegrationService,
)
from app.services.outbound_provider_status_events import (
    DEFAULT_PROVIDER_DELIVERY_STATUS_EVENT_LIMIT,
    MAX_PROVIDER_DELIVERY_STATUS_EVENT_LIMIT,
    OutboundProviderDeliveryStatusEventService,
    ProviderDeliveryStatusEventActionNotFoundError,
    ProviderDeliveryStatusEventValidationError,
)
from app.services.outbound_retry_delay_policy import OutboundDeliveryRetryDelayPolicy
from app.services.outbound_retry_policy import OutboundDeliveryRetryPolicy
from app.services.repository import NotFoundError
from app.services.secret_reference_policy import SecretReferenceValidationError
from app.services.whatsapp_cloud import WhatsAppCloudOutboundPayloadSecretError

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _utc_timestamp(value: datetime | None) -> datetime | None:
    """Render persisted scheduling timestamps explicitly in UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

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

OutboundActionPriorityFilter = Annotated[
    OutboundActionPriority | None,
    Query(description="Filter outbound actions by their provider-neutral priority."),
]

DeliveryAttemptLimit = Annotated[
    int,
    Query(
        ge=1,
        le=MAX_DELIVERY_ATTEMPT_LIMIT,
        description="Maximum number of safe delivery attempts to return.",
    ),
]

ProviderDeliveryStatusEventLimit = Annotated[
    int,
    Query(
        ge=1,
        le=MAX_PROVIDER_DELIVERY_STATUS_EVENT_LIMIT,
        description="Maximum number of safe provider status events to return.",
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

OutboundStateHistoryLimit = Annotated[
    int,
    Query(
        ge=1,
        le=MAX_OUTBOUND_STATE_HISTORY_LIMIT,
        description="Maximum number of safe outbound state transitions to return.",
    ),
]

OutboundActionTimelineLimit = Annotated[
    int,
    Query(
        ge=1,
        le=MAX_OUTBOUND_ACTION_TIMELINE_LIMIT,
        description="Maximum number of safe outbound timeline entries to return.",
    ),
]
OutboundActionAnnotationLimit = Annotated[int, Query(ge=1, le=100)]
OutboundActionLabelLimit = Annotated[int, Query(ge=1, le=MAX_OUTBOUND_ACTION_LABELS)]

OutboundActionTimelineCategoryFilter = Annotated[
    OutboundActionTimelineCategory | None,
    Query(description="Filter safe outbound timeline entries by category."),
]

OutboundActionTimelineEventFilter = Annotated[
    OutboundActionTimelineEvent | None,
    Query(description="Filter safe outbound timeline entries by event type."),
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
        requires_approval=action.requires_approval,
        approval_request_id=action.approval_request_id,
        owner_reference=action.owner_reference,
        archived_at=action.archived_at,
        status=action.status,
        priority=action.priority,
        provider_delivery_id=action.provider_delivery_id,
        delivered_at=action.delivered_at,
        failed_at=action.failed_at,
        cancelled_at=action.cancelled_at,
        not_before=_utc_timestamp(action.not_before),
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
        priority=action.priority,
        owner_reference=action.owner_reference,
        archived_at=action.archived_at,
        provider_delivery_id=action.provider_delivery_id,
        delivered_at=action.delivered_at,
        failed_at=action.failed_at,
        cancelled_at=action.cancelled_at,
        not_before=_utc_timestamp(action.not_before),
        expires_at=action.expires_at,
        expired_at=action.expired_at,
        failure_code=action.failure_code,
        created_at=action.created_at,
    )


def outbound_action_annotation_read(annotation: OutboundActionAnnotation) -> OutboundActionAnnotationRead:
    return OutboundActionAnnotationRead(
        id=annotation.id, outbound_integration_action_id=annotation.outbound_integration_action_id,
        text=annotation.text, created_at=annotation.created_at
    )


def outbound_action_label_read(label: OutboundActionLabel) -> OutboundActionLabelRead:
    return OutboundActionLabelRead(label=label.label, created_at=label.created_at)


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


def provider_delivery_status_event_read(
    event,
) -> ProviderDeliveryStatusEventRead:
    return ProviderDeliveryStatusEventRead.model_validate(event)


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
        archived_at=action.archived_at,
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


def integration_execution_trace_read(
    view: IntegrationExecutionTraceView,
) -> IntegrationExecutionTraceRead:
    """Render only established safe fields from the trace service's persisted view."""
    receipt = view.receipt.receipt
    receipt_account = view.receipt.account
    return IntegrationExecutionTraceRead(
        correlation_id=receipt.correlation_id,
        inbound=IntegrationExecutionInboundReceiptRead(
            integration_account_id=receipt.integration_account_id,
            provider=receipt_account.provider,
            external_account_id=receipt_account.external_account_id,
            external_event_id=receipt.external_event_id,
            correlation_id=receipt.correlation_id,
            received_at=receipt.created_at,
        ),
        outbound_actions=[
            IntegrationExecutionOutboundActionRead(
                id=outbound.action.id,
                integration_account_id=outbound.action.integration_account_id,
                provider=outbound.account.provider,
                external_target_id=outbound.action.external_target_id,
                action_type=outbound.action.action_type,
                status=outbound.action.status,
                requires_approval=outbound.action.requires_approval,
                approval_request_id=outbound.action.approval_request_id,
                approval_status=(
                    outbound.approval.status if outbound.approval is not None else None
                ),
                provider_delivery_id=outbound.action.provider_delivery_id,
                delivered_at=outbound.action.delivered_at,
                failed_at=outbound.action.failed_at,
                cancelled_at=outbound.action.cancelled_at,
                expired_at=outbound.action.expired_at,
                created_at=outbound.action.created_at,
                delivery_attempts=[
                    IntegrationExecutionDeliveryAttemptRead(
                        id=attempt.id,
                        attempt_number=attempt.attempt_number,
                        status=attempt.status,
                        provider_delivery_id=attempt.provider_delivery_id,
                        started_at=attempt.started_at,
                        completed_at=attempt.completed_at,
                        failure_code=attempt.failure_code,
                        failure_message=attempt.failure_message,
                    )
                    for attempt in outbound.delivery_attempts
                ],
            )
            for outbound in view.outbound_actions
        ],
    )


@router.get(
    "/outbound-audit-events",
    response_model=list[OutboundIntegrationAuditEventRead],
)
def list_outbound_integration_audit_events(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: IntegrationReadPermissionDep,
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
    context: AuthenticatedWorkspaceContextDep,
    _: IntegrationManagePermissionDep,
) -> IntegrationAccountCredentialRead:
    """Provision a workspace-owned account and return its credential once."""
    try:
        account, credential = IntegrationAccountService(session).provision(
            workspace,
            payload.provider,
            payload.external_account_id,
            payload.secret_reference,
            payload.provider_auth_mode,
            actor_user_id=context.principal.user_id,
        )
    except IntegrationAccountOwnershipConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (
        SecretReferenceValidationError,
        IntegrationAccountProviderAuthModeError,
        IntegrationAccountProviderValidationError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Secret reference is not allowed"
                if isinstance(exc, SecretReferenceValidationError)
                else str(exc)
            ),
        ) from exc
    return account_credential_read(account, credential)


@router.get("/accounts", response_model=list[IntegrationAccountRead])
def list_integration_accounts(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: IntegrationReadPermissionDep,
) -> list[IntegrationAccountRead]:
    accounts = IntegrationAccountService(session).list_for_workspace(workspace)
    return [account_read(account) for account in accounts]


@router.get("/operational-summary", response_model=IntegrationOperationalSummaryRead)
def get_integration_operational_summary(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: IntegrationReadPermissionDep,
    settings: SettingsDep,
) -> IntegrationOperationalSummaryRead:
    """Read safe workspace aggregates without provider calls or mutation."""
    summary = IntegrationOperationalSummaryService(
        session,
        OutboundDeliveryRetryPolicy.from_settings(settings),
    ).summarize(
        workspace,
        window_days=settings.integration_health_window_days,
        now=utc_now(),
    )
    return IntegrationOperationalSummaryRead(**summary.__dict__)


@router.get("/ai-usage/summary", response_model=AIInvocationUsageSummaryRead)
def get_workspace_ai_invocation_usage_summary(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: AIUsageReadPermissionDep,
) -> AIInvocationUsageSummaryRead:
    """Read deterministic aggregate AI usage for the current workspace only."""

    summary = AIInvocationUsageService(session).summarize_for_workspace(workspace)
    return AIInvocationUsageSummaryRead(**summary.__dict__)


@router.get("/ai-usage", response_model=list[AIInvocationUsageRead])
def list_workspace_ai_invocation_usage(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: AIUsageReadPermissionDep,
) -> list[AIInvocationUsageRead]:
    """Read safe AI usage metadata for the current workspace only."""
    return [
        AIInvocationUsageRead.model_validate(usage)
        for usage in AIInvocationUsageService(session).list_for_workspace(workspace)
    ]


@router.get("/accounts/{account_id}/health", response_model=IntegrationAccountHealthRead)
def get_integration_account_health(
    account_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: IntegrationReadPermissionDep,
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
        connection_status=view.account.connection_status,
        last_validated_at=view.account.last_validated_at,
        credential_references_ready=view.credential_references_ready,
        credential_expired=view.credential_expired,
        health=view.health,
        most_recent_outbound_at=view.most_recent_outbound_at,
        recent_delivered_count=view.recent_delivered_count,
        recent_failed_count=view.recent_failed_count,
        pending_action_count=view.pending_action_count,
        failed_action_count=view.failed_action_count,
    )


@router.get(
    "/accounts/{account_id}/health/runtime-readiness",
    response_model=IntegrationRuntimeReadinessRead,
)
def get_integration_runtime_readiness(
    account_id: UUID,
    session: SessionDep,
    workspace: WorkspaceReadinessDep,
    _: IntegrationReadinessReadPermissionDep,
    settings: SettingsDep,
) -> IntegrationRuntimeReadinessRead:
    """Read configuration readiness only; never contacts an external provider."""
    try:
        delivery_service = OutboundIntegrationDeliveryService.from_settings(session, settings)
        view = IntegrationRuntimeReadinessService(
            session,
            settings,
            delivery_service.adapter_registry,
        ).evaluate(workspace, account_id)
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    return IntegrationRuntimeReadinessRead(
        id=view.account.id,
        provider=view.account.provider,
        active=view.account.active,
        connection_status=view.account.connection_status,
        status="ready" if view.configuration_ready else "blocked",
        configuration_ready=view.configuration_ready,
        external_provider_availability_checked=False,
        supported_capabilities=[
            str(capability.capability)
            for capability in view.capabilities
            if capability.supported
        ],
        capability_readiness=[
            {
                "capability": str(capability.capability),
                "supported": capability.supported,
                "ready": capability.ready,
                "blocking_reasons": [
                    str(blocker.code) for blocker in capability.blockers
                ],
                "blocking_reason_details": [
                    {"code": str(blocker.code), "message": blocker.message}
                    for blocker in capability.blockers
                ],
            }
            for capability in view.capabilities
        ],
        blocking_reasons=[str(blocker.code) for blocker in view.blockers],
        blocking_reason_details=[
            {"code": str(blocker.code), "message": blocker.message}
            for blocker in view.blockers
        ],
    )


@router.get(
    "/accounts/{account_id}/audit-events",
    response_model=list[IntegrationAccountAuditEventRead],
)
def list_integration_account_audit_events(
    account_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: IntegrationReadPermissionDep,
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
    _: IntegrationReadPermissionDep,
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
    _: IntegrationManagePermissionDep,
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
    context: AuthenticatedWorkspaceContextDep,
    _: IntegrationManagePermissionDep,
) -> IntegrationAccountRead:
    try:
        account = ChannelConnectionService(session).disable(
            workspace,
            account_id,
            actor_user_id=context.principal.user_id,
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    return account_read(account)


@router.post("/accounts/{account_id}/reactivate", response_model=IntegrationAccountRead)
def reactivate_integration_account(
    account_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    context: AuthenticatedWorkspaceContextDep,
    _: IntegrationManagePermissionDep,
) -> IntegrationAccountRead:
    try:
        account = ChannelConnectionService(session).enable(
            workspace,
            account_id,
            actor_user_id=context.principal.user_id,
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    except IntegrationAccountOwnershipConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ChannelConnectionLifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return account_read(account)


@router.post("/accounts/{account_id}/disconnect", response_model=IntegrationAccountRead)
def disconnect_integration_account(
    account_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    context: AuthenticatedWorkspaceContextDep,
    _: IntegrationManagePermissionDep,
) -> IntegrationAccountRead:
    """Disconnect HIRI while preserving business and audit history."""

    try:
        account = ChannelConnectionService(session).disconnect(
            workspace,
            account_id,
            actor_user_id=context.principal.user_id,
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    return account_read(account)


@router.put(
    "/accounts/{account_id}/comment-to-message-eligibility",
    response_model=IntegrationAccountRead,
)
def update_comment_to_message_eligibility(
    account_id: UUID,
    payload: IntegrationAccountCommentToMessageEligibilityUpdate,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    context: AuthenticatedWorkspaceContextDep,
    _: IntegrationManagePermissionDep,
) -> IntegrationAccountRead:
    """Record operator-confirmed TikTok Comment-to-Message eligibility."""
    try:
        account = IntegrationAccountService(session).set_comment_to_message_eligibility(
            workspace,
            account_id,
            eligible=payload.eligible,
            actor_user_id=context.principal.user_id,
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    except IntegrationAccountProviderValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return account_read(account)


@router.post(
    "/accounts/{account_id}/credential/rotate",
    response_model=IntegrationAccountCredentialRead,
)
def rotate_integration_account_credential(
    account_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    context: AuthenticatedWorkspaceContextDep,
    _: IntegrationManagePermissionDep,
) -> IntegrationAccountCredentialRead:
    try:
        account, credential = IntegrationAccountService(session).rotate_credential(
            workspace,
            account_id,
            actor_user_id=context.principal.user_id,
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
    context: AuthenticatedWorkspaceContextDep,
    _: IntegrationManagePermissionDep,
) -> IntegrationAccountRead:
    """Update an account's internal secret reference without resolving it."""
    try:
        account = IntegrationAccountService(session).update_secret_reference(
            workspace,
            account_id,
            payload.secret_reference,
            actor_user_id=context.principal.user_id,
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    except SecretReferenceValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Secret reference is not allowed",
        ) from exc
    return account_read(account)

@router.put(
    "/accounts/{account_id}/credential-references/{purpose}",
    response_model=IntegrationCredentialReferenceRead,
)
def set_integration_credential_reference(
    account_id: UUID,
    purpose: str,
    payload: IntegrationCredentialReferenceUpsert,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    context: AuthenticatedWorkspaceContextDep,
    _permission: IntegrationManagePermissionDep,
) -> IntegrationCredentialReferenceRead:
    try:
        reference = IntegrationCredentialReferenceService(session).set_reference(
            workspace,
            account_id,
            purpose,
            payload.secret_reference,
            expires_at=payload.expires_at,
            actor_user_id=context.principal.user_id,
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration account not found",
        ) from exc
    except (
        IntegrationCredentialPurposeValidationError,
        SecretReferenceValidationError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    return IntegrationCredentialReferenceRead.model_validate(reference)


@router.get(
    "/accounts/{account_id}/credential-references",
    response_model=list[IntegrationCredentialReferenceRead],
)
def list_integration_credential_references(
    account_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _permission: IntegrationReadPermissionDep,
) -> list[IntegrationCredentialReferenceRead]:
    try:
        references = IntegrationCredentialReferenceService(session).list_for_account(
            workspace,
            account_id,
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration account not found",
        ) from exc

    return [
        IntegrationCredentialReferenceRead.model_validate(reference)
        for reference in references
    ]

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
    _: OutboundActionOperatePermissionDep,
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
            requires_approval=payload.requires_approval,
            not_before=payload.not_before,
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    except (
        InactiveIntegrationAccountError,
        IntegrationAccountReconnectRequiredError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OutboundIntegrationActionIdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WhatsAppCloudOutboundPayloadSecretError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return outbound_action_read(action, account)


@router.get(
    "/outbound-actions",
    response_model=list[OutboundIntegrationActionSummaryRead],
)
def list_outbound_integration_actions(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: IntegrationReadPermissionDep,
    action_status: OutboundActionStatusFilter = None,
    priority: OutboundActionPriorityFilter = None,
    label: str | None = Query(default=None, min_length=1, max_length=64),
    owner_reference: str | None = Query(default=None, min_length=1, max_length=200),
    unowned: bool | None = Query(
        default=None,
        description="Filter actions by whether their opaque owner reference is absent.",
    ),
    archived: bool | None = Query(
        default=None,
        description="Filter actions by their non-destructive archive state.",
    ),
    provider: str | None = Query(default=None, min_length=1, max_length=100),
    integration_account_id: UUID | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: OutboundActionLimit = DEFAULT_OUTBOUND_ACTION_LIMIT,
) -> list[OutboundIntegrationActionSummaryRead]:
    """List safe outbound action summaries from the current workspace only."""
    try:
        normalized_label = (
            OutboundActionLabelService.normalize(label) if label is not None else None
        )
        normalized_owner_reference = (
            OutboundActionOwnershipService.normalize(owner_reference)
            if owner_reference is not None
            else None
        )
        rows = OutboundIntegrationActionQueryService(session).list_for_workspace(
            workspace,
            action_status=action_status,
            priority=priority,
            label=normalized_label,
            owner_reference=normalized_owner_reference,
            unowned=unowned,
            archived=archived,
            provider=provider,
            integration_account_id=integration_account_id,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
        )
    except OutboundActionQueryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OutboundActionLabelValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OutboundActionOwnerReferenceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [outbound_action_summary_read(action, provider_name) for action, provider_name in rows]


@router.post("/outbound-actions/expiration-cleanup", response_model=OutboundActionExpirationCleanupRead)
def cleanup_expired_outbound_actions(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: IntegrationManagePermissionDep,
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
    _: IntegrationReadPermissionDep,
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


@router.post("/outbound-actions/{action_id}/annotations", response_model=OutboundActionAnnotationRead, status_code=status.HTTP_201_CREATED)
def create_outbound_action_annotation(action_id: UUID, payload: OutboundActionAnnotationCreate, session: SessionDep, workspace: CurrentWorkspaceDep, _: OutboundActionOperatePermissionDep) -> OutboundActionAnnotationRead:
    try:
        annotation = OutboundActionAnnotationService(session).create(workspace, action_id, payload.text)
    except OutboundIntegrationActionQueryNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Outbound integration action not found") from exc
    return outbound_action_annotation_read(annotation)


@router.get("/outbound-actions/{action_id}/annotations", response_model=list[OutboundActionAnnotationRead])
def list_outbound_action_annotations(action_id: UUID, session: SessionDep, workspace: CurrentWorkspaceDep, _: IntegrationReadPermissionDep, limit: OutboundActionAnnotationLimit = 50) -> list[OutboundActionAnnotationRead]:
    try:
        rows = OutboundActionAnnotationService(session).list_for_action(workspace, action_id, limit=limit)
    except OutboundIntegrationActionQueryNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Outbound integration action not found") from exc
    return [outbound_action_annotation_read(row) for row in rows]


@router.post(
    "/outbound-actions/{action_id}/labels",
    response_model=OutboundActionLabelRead,
    status_code=status.HTTP_201_CREATED,
)
def add_outbound_action_label(
    action_id: UUID,
    payload: OutboundActionLabelCreate,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: OutboundActionOperatePermissionDep,
) -> OutboundActionLabelRead:
    try:
        label = OutboundActionLabelService(session).add(workspace, action_id, payload.label)
    except OutboundIntegrationActionQueryNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Outbound integration action not found") from exc
    except OutboundActionLabelValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return outbound_action_label_read(label)


@router.get(
    "/outbound-actions/{action_id}/labels",
    response_model=list[OutboundActionLabelRead],
)
def list_outbound_action_labels(
    action_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: IntegrationReadPermissionDep,
    limit: OutboundActionLabelLimit = MAX_OUTBOUND_ACTION_LABELS,
) -> list[OutboundActionLabelRead]:
    try:
        labels = OutboundActionLabelService(session).list_for_action(
            workspace, action_id, limit=limit
        )
    except OutboundIntegrationActionQueryNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Outbound integration action not found") from exc
    return [outbound_action_label_read(label) for label in labels]


@router.delete(
    "/outbound-actions/{action_id}/labels/{label}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_outbound_action_label(
    action_id: UUID,
    label: str,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: OutboundActionOperatePermissionDep,
) -> None:
    try:
        OutboundActionLabelService(session).remove(workspace, action_id, label)
    except OutboundIntegrationActionQueryNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Outbound integration action not found") from exc
    except OutboundActionLabelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OutboundActionLabelValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/outbound-actions/{action_id}/priority", response_model=OutboundIntegrationActionDetailRead)
def update_outbound_action_priority(action_id: UUID, payload: OutboundActionPriorityUpdate, session: SessionDep, workspace: CurrentWorkspaceDep, _: OutboundActionOperatePermissionDep) -> OutboundIntegrationActionDetailRead:
    try:
        action = OutboundIntegrationActionQueryService(session).set_priority(workspace, action_id, payload.priority)
        _, provider = OutboundIntegrationActionQueryService(session).get_for_workspace(workspace, action_id)
    except OutboundIntegrationActionQueryNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Outbound integration action not found") from exc
    return outbound_action_detail_read(action, provider)


@router.put(
    "/outbound-actions/{action_id}/owner-reference",
    response_model=OutboundIntegrationActionDetailRead,
)
def update_outbound_action_owner_reference(
    action_id: UUID,
    payload: OutboundActionOwnerReferenceUpdate,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: OutboundActionOperatePermissionDep,
) -> OutboundIntegrationActionDetailRead:
    """Set an unverified opaque owner reference without affecting delivery behavior."""
    try:
        action = OutboundActionOwnershipService(session).set_owner_reference(
            workspace, action_id, payload.owner_reference
        )
        _, provider = OutboundIntegrationActionQueryService(session).get_for_workspace(
            workspace, action_id
        )
    except OutboundIntegrationActionQueryNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Outbound integration action not found") from exc
    except OutboundActionOwnerReferenceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return outbound_action_detail_read(action, provider)


@router.post(
    "/outbound-actions/{action_id}/archive",
    response_model=OutboundIntegrationActionDetailRead,
)
def archive_outbound_integration_action(
    action_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: OutboundActionOperatePermissionDep,
) -> OutboundIntegrationActionDetailRead:
    """Archive one terminal action without changing delivery state or history."""
    try:
        action = OutboundActionArchivingService(session).archive(workspace, action_id)
        _, provider = OutboundIntegrationActionQueryService(session).get_for_workspace(
            workspace, action_id
        )
    except OutboundIntegrationActionQueryNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Outbound integration action not found") from exc
    except (
        OutboundIntegrationActionNotArchivableError,
        OutboundIntegrationActionAlreadyArchivedError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return outbound_action_detail_read(action, provider)


@router.post(
    "/outbound-actions/{action_id}/unarchive",
    response_model=OutboundIntegrationActionDetailRead,
)
def unarchive_outbound_integration_action(
    action_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: OutboundActionOperatePermissionDep,
) -> OutboundIntegrationActionDetailRead:
    """Restore a previously archived action without changing its delivery state."""
    try:
        action = OutboundActionArchivingService(session).unarchive(workspace, action_id)
        _, provider = OutboundIntegrationActionQueryService(session).get_for_workspace(
            workspace, action_id
        )
    except OutboundIntegrationActionQueryNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Outbound integration action not found") from exc
    except OutboundIntegrationActionNotArchivedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    _: OutboundActionOperatePermissionDep,
    rate_limit: OutboundDeliveryRateLimitDep,
    settings: SettingsDep,
) -> OutboundIntegrationActionRead:
    """Explicitly process one pending action through a neutral adapter."""
    del rate_limit
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
    except (
        InactiveIntegrationAccountError,
        IntegrationAccountReconnectRequiredError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OutboundIntegrationActionAlreadyProcessedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OutboundIntegrationActionExpiredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OutboundIntegrationActionNotReadyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OutboundDeliveryApprovalRequiredError, OutboundDeliveryApprovalRejectedError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return outbound_action_read(action, account)


@router.post(
    "/accounts/{account_id}/outbound-actions/{action_id}/cancel",
    response_model=OutboundIntegrationActionRead,
)
def cancel_outbound_integration_action(
    account_id: UUID,
    action_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: OutboundActionOperatePermissionDep,
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
    _: OutboundActionOperatePermissionDep,
    rate_limit: OutboundDeliveryRateLimitDep,
    settings: SettingsDep,
) -> OutboundIntegrationActionRead:
    """Explicitly retry one failed action with the same persisted identity."""
    del rate_limit
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
    except (
        InactiveIntegrationAccountError,
        IntegrationAccountReconnectRequiredError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OutboundIntegrationActionRetryDeniedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OutboundIntegrationActionNotRetryableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return outbound_action_read(action, account)


@router.get(
    "/accounts/{account_id}/outbound-actions/{action_id}/delivery-readiness",
    response_model=OutboundDeliveryReadinessRead,
)
def get_outbound_delivery_readiness(
    account_id: UUID,
    action_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: IntegrationReadPermissionDep,
    settings: SettingsDep,
) -> OutboundDeliveryReadinessRead:
    """Read readiness only; this endpoint never executes a delivery operation."""
    try:
        delivery_service = OutboundIntegrationDeliveryService.from_settings(
            session,
            settings,
            retry_policy=OutboundDeliveryRetryPolicy.from_settings(settings),
        )
        view = OutboundDeliveryReadinessService(
            session,
            retry_policy=OutboundDeliveryRetryPolicy.from_settings(settings),
            retry_delay_policy=OutboundDeliveryRetryDelayPolicy.from_settings(settings),
            adapter_registry=delivery_service.adapter_registry,
        ).evaluate(workspace, account_id, action_id)
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    except OutboundIntegrationActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Outbound integration action not found") from exc
    return OutboundDeliveryReadinessRead(
        action_id=view.action_id,
        status=view.status,
        ready=view.ready,
        blocking_reasons=list(view.blocking_reasons),
        next_retry_at=view.next_retry_at,
        blocking_reason_details=[
            {
                "code": detail.code,
                "message": detail.message,
                "not_before": detail.not_before,
                "expires_at": detail.expires_at,
                "next_retry_at": detail.next_retry_at,
            }
            for detail in view.blocking_reason_details
        ],
    )


@router.get(
    "/accounts/{account_id}/outbound-actions/{action_id}/approval-status",
    response_model=OutboundApprovalStatusRead,
)
def get_outbound_approval_status(
    account_id: UUID,
    action_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: IntegrationReadPermissionDep,
) -> OutboundApprovalStatusRead:
    """Return safe approval state without approving, delivering, or mutating."""
    try:
        view = OutboundApprovalStatusService(session).get_for_action(
            workspace, account_id, action_id
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    except OutboundApprovalStatusNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return OutboundApprovalStatusRead(**view.__dict__)


@router.get(
    "/accounts/{account_id}/outbound-actions/{action_id}/state-history",
    response_model=list[OutboundActionStateHistoryEntryRead],
)
def list_outbound_action_state_history(
    account_id: UUID,
    action_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: IntegrationReadPermissionDep,
    limit: OutboundStateHistoryLimit = DEFAULT_OUTBOUND_STATE_HISTORY_LIMIT,
) -> list[OutboundActionStateHistoryEntryRead]:
    """Read successful transitions only; this endpoint never changes an action."""
    try:
        entries = OutboundActionStateHistoryService(session).list_for_action(
            workspace, account_id, action_id, limit=limit
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    except OutboundIntegrationActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Outbound integration action not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [OutboundActionStateHistoryEntryRead(**entry.__dict__) for entry in entries]


@router.get(
    "/accounts/{account_id}/outbound-actions/{action_id}/timeline",
    response_model=list[OutboundActionTimelineEntryRead],
)
def list_outbound_action_timeline(
    account_id: UUID,
    action_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: IntegrationReadPermissionDep,
    category: OutboundActionTimelineCategoryFilter = None,
    event: OutboundActionTimelineEventFilter = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: OutboundActionTimelineLimit = DEFAULT_OUTBOUND_ACTION_TIMELINE_LIMIT,
) -> list[OutboundActionTimelineEntryRead]:
    """Read safe outbound history without delivering, approving, or mutating."""
    try:
        entries = OutboundActionTimelineService(session).list_for_action(
            workspace,
            account_id,
            action_id,
            category=category,
            event=event,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    except OutboundIntegrationActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Outbound integration action not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [OutboundActionTimelineEntryRead(**entry.__dict__) for entry in entries]


@router.get(
    "/accounts/{account_id}/outbound-actions/{action_id}/transition-validation",
    response_model=OutboundActionTransitionValidationRead,
)
def validate_outbound_action_transition(
    account_id: UUID,
    action_id: UUID,
    target: OutboundIntegrationActionStatus,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: IntegrationReadPermissionDep,
) -> OutboundActionTransitionValidationRead:
    """Validate a proposed state change without mutating or auditing the action."""
    try:
        result = OutboundActionTransitionValidationService(session).validate(
            workspace, account_id, action_id, target
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    except OutboundIntegrationActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Outbound integration action not found") from exc
    explanation = result.denial_reason_detail
    return OutboundActionTransitionValidationRead(
        allowed=result.allowed,
        current_state=result.current_state,
        requested_target=result.requested_target,
        denial_reason=result.denial_reason,
        denial_reason_detail=(
            OutboundActionTransitionExplanationRead(
                code=explanation.code,
                message=explanation.message,
                delivered_at=explanation.delivered_at,
                cancelled_at=explanation.cancelled_at,
                expired_at=explanation.expired_at,
            )
            if explanation is not None
            else None
        ),
    )


@router.get(
    "/accounts/{account_id}/outbound-actions/{action_id}/delivery-status",
    response_model=OutboundIntegrationDeliveryStatusRead,
)
def get_outbound_integration_delivery_status(
    account_id: UUID,
    action_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: IntegrationReadPermissionDep,
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
    "/accounts/{account_id}/outbound-actions/{action_id}/provider-status-events",
    response_model=list[ProviderDeliveryStatusEventRead],
)
def list_outbound_provider_delivery_status_events(
    account_id: UUID,
    action_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: IntegrationReadPermissionDep,
    limit: ProviderDeliveryStatusEventLimit = (
        DEFAULT_PROVIDER_DELIVERY_STATUS_EVENT_LIMIT
    ),
) -> list[ProviderDeliveryStatusEventRead]:
    """Return safe provider callback history for one scoped outbound action."""
    try:
        events = OutboundProviderDeliveryStatusEventService(session).list_for_action(
            workspace,
            account_id,
            action_id,
            limit=limit,
        )
    except IntegrationAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    except OutboundIntegrationActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Outbound integration action not found") from exc
    except ProviderDeliveryStatusEventValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [provider_delivery_status_event_read(event) for event in events]


@router.get(
    "/accounts/{account_id}/outbound-actions/{action_id}/delivery-attempts",
    response_model=list[OutboundIntegrationDeliveryAttemptRead],
)
def list_outbound_integration_delivery_attempts(
    account_id: UUID,
    action_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: IntegrationReadPermissionDep,
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


@router.get(
    "/execution-traces/{correlation_id}",
    response_model=IntegrationExecutionTraceRead,
)
def get_integration_execution_trace(
    correlation_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: IntegrationReadPermissionDep,
) -> IntegrationExecutionTraceRead:
    """Read one safe execution trace from existing workspace-scoped records only."""
    try:
        view = IntegrationExecutionTraceService(session).get_for_workspace(
            workspace,
            correlation_id,
        )
    except IntegrationExecutionTraceNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="Integration execution trace not found",
        ) from exc
    return integration_execution_trace_read(view)


@router.post(
    "/inbound-events/provider-status-events",
    response_model=ProviderDeliveryStatusEventIngestRead,
)
def receive_provider_delivery_status_event(
    payload: ProviderDeliveryStatusEventCreate,
    session: SessionDep,
    integration_context: VerifiedIntegrationContextDep,
    rate_limit: IntegrationIngestRateLimitDep,
) -> ProviderDeliveryStatusEventIngestRead:
    """Accept a machine-authenticated provider delivery-status callback."""
    del rate_limit
    try:
        result = OutboundProviderDeliveryStatusEventService(session).record_event(
            integration_context.workspace,
            integration_context.account,
            provider_delivery_id=payload.provider_delivery_id,
            provider_status=payload.provider_status,
            provider_timestamp=payload.provider_timestamp,
            provider_error_code=payload.provider_error_code,
            provider_error_title=payload.provider_error_title,
            provider_error_type=payload.provider_error_type,
            failure_classification=payload.failure_classification,
        )
    except ProviderDeliveryStatusEventActionNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="Outbound integration action not found",
        ) from exc
    return ProviderDeliveryStatusEventIngestRead(
        duplicate=result.duplicate,
        event=provider_delivery_status_event_read(result.event),
    )


@router.post(
    "/inbound-events",
    response_model=InboundIntegrationReplyRead | SalesReply | InboundIntegrationDuplicateRead,
)
async def receive_inbound_event(
    payload: InboundIntegrationEvent,
    session: SessionDep,
    integration_context: VerifiedIntegrationContextDep,
    rate_limit: IntegrationIngestRateLimitDep,
    settings: SettingsDep,
    request: Request,
) -> InboundIntegrationReplyRead | SalesReply | InboundIntegrationDuplicateRead:
    """Accept an inbound event; the optional header enables durable retry safety."""
    del rate_limit

    integration_service = InboundIntegrationService(session, settings)
    integration_event_id = request.headers.get("X-Integration-Event-Id")
    reservation = None

    try:
        if integration_event_id is not None:
            reservation = integration_service.reserve_event(
                integration_context.workspace,
                integration_context.account,
                integration_event_id,
            )
            if not reservation.first_delivery:
                return InboundIntegrationDuplicateRead(
                    correlation_id=reservation.receipt.correlation_id,
                )
        result = await integration_service.handle_event(
            payload,
            integration_context.workspace,
        )
    except InboundIntegrationEventIdValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lead not found") from exc

    reply = {
        "lead_id": payload.lead_id,
        "detected_stage": result.detected_stage,
        "draft_reply": result.draft_reply,
        "approval_id": result.approval_id,
        "handoff_required": result.handoff_required,
        "handoff_reason_code": result.handoff_reason_code,
    }
    if reservation is not None:
        return InboundIntegrationReplyRead(
            **reply,
            correlation_id=reservation.receipt.correlation_id,
        )
    return SalesReply(
        **reply,
    )
