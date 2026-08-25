from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.dependencies import (
    SessionDep,
    SettingsDep,
    TikTokIntegrationIngestRateLimitDep,
    VerifiedTikTokIntegrationContextDep,
)
from app.core.lead_capture import LeadCaptureSignal
from app.schemas import (
    InboundIntegrationDuplicateRead,
    InboundIntegrationEvent,
    InboundIntegrationReplyRead,
    TikTokCommentIntakeRead,
    TikTokInboundIgnoredRead,
)
from app.services.inbound_integrations import (
    InboundIntegrationEventIdValidationError,
    InboundIntegrationService,
    InboundSalesWorkItemRoutingError,
)
from app.services.meta_inbound import (
    AmbiguousExternalIdentityLeadError,
    InboundExternalIdentityBindingError,
    InboundExternalIdentityService,
)
from app.services.repository import NotFoundError
from app.services.social_comment_automation import SocialCommentAutomationService
from app.services.tiktok_business import (
    TikTokInboundAccountMismatchError,
    TikTokInboundNormalizationError,
    TikTokInboundNormalizer,
    TikTokInboundUnsupportedEventError,
)
from app.services.workspaces import ensure_workspace_lead_capture_foundation

router = APIRouter(
    prefix="/integrations/inbound-events/tiktok",
    tags=["integrations"],
)


@router.post(
    "",
    response_model=(
        InboundIntegrationReplyRead
        | InboundIntegrationDuplicateRead
        | TikTokCommentIntakeRead
        | TikTokInboundIgnoredRead
    ),
)
async def receive_tiktok_event(
    payload: dict[str, Any],
    session: SessionDep,
    integration_context: VerifiedTikTokIntegrationContextDep,
    rate_limit: TikTokIntegrationIngestRateLimitDep,
    settings: SettingsDep,
) -> (
    InboundIntegrationReplyRead
    | InboundIntegrationDuplicateRead
    | TikTokCommentIntakeRead
    | TikTokInboundIgnoredRead
):
    """Process one authenticated TikTok Business Messaging callback."""
    del rate_limit
    account = integration_context.account
    workspace = integration_context.workspace
    try:
        event = TikTokInboundNormalizer().normalize(
            payload,
            expected_app_id=settings.tiktok_business_app_id,
            expected_account_id=account.external_account_id,
        )
    except TikTokInboundUnsupportedEventError:
        event_name = payload.get("event")
        return TikTokInboundIgnoredRead(
            event=event_name if isinstance(event_name, str) else None
        )
    except TikTokInboundAccountMismatchError as exc:
        raise HTTPException(status_code=404, detail="Integration account not found") from exc
    except TikTokInboundNormalizationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    ensure_workspace_lead_capture_foundation(session, workspace)
    inbound = InboundIntegrationService(session, settings)
    try:
        reservation = inbound.reserve_event(
            workspace,
            account,
            event.provider_event_id,
        )
        if not reservation.first_delivery:
            return InboundIntegrationDuplicateRead(
                correlation_id=reservation.receipt.correlation_id
            )

        with inbound.release_event_reservation_on_failure(
            workspace,
            account,
            reservation,
        ):
            if event.kind == "comment":
                automation = SocialCommentAutomationService(session, settings).process(
                    workspace,
                    account,
                    event,
                    reservation,
                    inbound,
                )
                return TikTokCommentIntakeRead(
                    channel=event.channel,
                    external_author_id=event.sender_external_id,
                    comment_id=event.provider_event_id,
                    post_or_media_id=None,
                    parent_comment_id=None,
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
                    inbound,
                    reservation,
                )
            except AmbiguousExternalIdentityLeadError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            capture = inbound.capture_reserved_event(
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
                        "conversation_id": event.external_conversation_id,
                        "timestamp": event.timestamp,
                        "message_type": event.message_type,
                    },
                    contact_id=identity.contact_id,
                    lead_id=(
                        recovered_lead.id if recovered_lead is not None else None
                    ),
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
            result = await inbound.handle_work_item_event(
                InboundIntegrationEvent(
                    lead_id=capture.lead_id,
                    channel=event.channel,
                    content=event.content,
                    external_event_id=event.provider_event_id,
                ),
                workspace,
                account,
                correlation_id=reservation.receipt.correlation_id,
                external_target_id=event.external_conversation_id,
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
