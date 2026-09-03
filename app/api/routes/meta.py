from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from sqlmodel import select

from app.api.dependencies import (
    MetaIntegrationIngestRateLimitDep,
    SessionDep,
    SettingsDep,
    VerifiedMetaIntegrationContextDep,
)
from app.core.lead_capture import LeadCaptureSignal
from app.integrations.providers import META_MESSAGING_PROVIDERS
from app.models import IntegrationAccount
from app.schemas import (
    InboundIntegrationDuplicateRead,
    InboundIntegrationEvent,
    InboundIntegrationReplyRead,
    MetaCommentIntakeRead,
)
from app.services.inbound_integrations import (
    InboundIntegrationEventIdValidationError,
    InboundIntegrationService,
    InboundSalesWorkItemRoutingError,
)
from app.services.integration_credential_references import (
    IntegrationCredentialReferenceNotFoundError,
    IntegrationCredentialReferenceService,
)
from app.services.meta_inbound import (
    AmbiguousExternalIdentityLeadError,
    InboundExternalIdentityBindingError,
    InboundExternalIdentityService,
    MetaInboundAccountMismatchError,
    MetaInboundNormalizationError,
    MetaInboundNormalizer,
    MetaWebhookVerificationError,
    verify_meta_webhook_challenge,
)
from app.services.repository import NotFoundError
from app.services.secret_resolver import EnvironmentSecretResolver
from app.services.social_comment_automation import SocialCommentAutomationService
from app.services.workspaces import ensure_workspace_lead_capture_foundation

router = APIRouter(prefix="/integrations/inbound-events/meta", tags=["integrations"])


@router.get("/{account_id}")
def verify_meta_webhook(
    account_id: UUID,
    request: Request,
    session: SessionDep,
) -> Response:
    """Verify a Messenger or Instagram callback URL for one account."""
    account = session.exec(
        select(IntegrationAccount).where(
            IntegrationAccount.id == account_id,
            IntegrationAccount.active.is_(True),
            IntegrationAccount.provider.in_(META_MESSAGING_PROVIDERS),
        )
    ).first()
    if account is None:
        raise HTTPException(status_code=401, detail="Invalid webhook verification")
    try:
        credential_reference = IntegrationCredentialReferenceService(
            session
        ).get_for_integration_account(account, "webhook_verify_token")
        configured_verify_token = EnvironmentSecretResolver().resolve(
            credential_reference.secret_reference
        )
        challenge = verify_meta_webhook_challenge(
            mode=request.query_params.get("hub.mode"),
            verify_token=request.query_params.get("hub.verify_token"),
            challenge=request.query_params.get("hub.challenge"),
            configured_verify_token=configured_verify_token,
        )
    except (
        IntegrationCredentialReferenceNotFoundError,
        MetaWebhookVerificationError,
    ) as exc:
        raise HTTPException(
            status_code=401,
            detail="Invalid webhook verification",
        ) from exc
    return Response(content=challenge, media_type="text/plain", status_code=200)


@router.post(
    "/{account_id}",
    response_model=(
        InboundIntegrationReplyRead
        | InboundIntegrationDuplicateRead
        | MetaCommentIntakeRead
    ),
)
async def receive_meta_event(
    account_id: UUID,
    payload: dict[str, Any],
    session: SessionDep,
    integration_context: VerifiedMetaIntegrationContextDep,
    rate_limit: MetaIntegrationIngestRateLimitDep,
    settings: SettingsDep,
) -> InboundIntegrationReplyRead | InboundIntegrationDuplicateRead | MetaCommentIntakeRead:
    """Authenticate and process one supported Facebook or Instagram event."""
    del account_id, rate_limit
    account = integration_context.account
    workspace = integration_context.workspace
    if account.provider not in META_MESSAGING_PROVIDERS:
        raise HTTPException(status_code=401, detail="Invalid webhook authentication")

    try:
        event = MetaInboundNormalizer().normalize(
            payload,
            provider=account.provider,
            expected_account_id=account.external_account_id,
        )
    except MetaInboundAccountMismatchError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    except MetaInboundNormalizationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    ensure_workspace_lead_capture_foundation(session, workspace)
    integration_service = InboundIntegrationService(session, settings)
    try:
        reservation = integration_service.reserve_event(
            workspace, account, event.provider_event_id
        )
        if not reservation.first_delivery:
            return InboundIntegrationDuplicateRead(
                correlation_id=reservation.receipt.correlation_id
            )
        if event.kind == "comment":
            with integration_service.release_event_reservation_on_failure(
                workspace, account, reservation
            ):
                automation = SocialCommentAutomationService(session, settings).process(
                    workspace,
                    account,
                    event,
                    reservation,
                    integration_service,
                )
            return MetaCommentIntakeRead(
                channel=event.channel,
                external_author_id=event.sender_external_id,
                comment_id=event.provider_event_id,
                post_or_media_id=event.post_or_media_id,
                parent_comment_id=event.parent_comment_id,
                content=event.content,
                timestamp=event.timestamp,
                correlation_id=reservation.receipt.correlation_id,
                trigger_result=automation.outcome,
                trigger_rule_id=(
                    automation.rule.id if automation.rule is not None else None
                ),
                lead_id=(
                    automation.capture.lead_id
                    if automation.capture is not None
                    else None
                ),
                work_item_id=(
                    automation.send.work_item.id
                    if automation.send is not None
                    else None
                ),
                approval_id=(
                    automation.send.approval_id
                    if automation.send is not None
                    else None
                ),
                outbound_action_id=(
                    automation.send.outbound_action.id
                    if automation.send is not None
                    and automation.send.outbound_action is not None
                    else None
                ),
            )

        identities = InboundExternalIdentityService(session)
        try:
            identity, recovered_lead = identities.prepare_for_capture(
                workspace,
                account,
                event,
                integration_service,
                reservation,
            )
        except AmbiguousExternalIdentityLeadError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        capture = integration_service.capture_reserved_event(
            workspace,
            account,
            reservation,
            LeadCaptureSignal(
                source=event.channel,
                name=event.display_name,
                message=event.content,
                external_reference=event.provider_event_id,
                metadata={
                    "account_id": event.recipient_account_id,
                    "timestamp": event.timestamp,
                    "message_type": event.message_type,
                },
                contact_id=identity.contact_id,
                lead_id=recovered_lead.id if recovered_lead is not None else None,
            ),
        )
        try:
            identities.bind_lead(
                workspace,
                account,
                identity,
                capture.lead_id,
            )
        except InboundExternalIdentityBindingError:
            pass
        result = await integration_service.handle_work_item_event(
            InboundIntegrationEvent(
                lead_id=capture.lead_id,
                channel=event.channel,
                content=event.content,
                external_event_id=event.provider_event_id,
            ),
            workspace,
            account,
            correlation_id=reservation.receipt.correlation_id,
            external_target_id=event.sender_external_id,
        )
    except InboundIntegrationEventIdValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lead not found") from exc
    except InboundSalesWorkItemRoutingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return InboundIntegrationReplyRead(
        lead_id=capture.lead_id,
        detected_stage=result.detected_stage,
        draft_reply=result.draft_reply,
        approval_id=result.approval_id,
        handoff_required=result.handoff_required,
        handoff_reason_code=result.handoff_reason_code,
        correlation_id=reservation.receipt.correlation_id,
    )
