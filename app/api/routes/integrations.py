from fastapi import APIRouter, HTTPException

from app.api.dependencies import (
    CurrentIntegrationWorkspaceDep,
    SessionDep,
    SettingsDep,
)
from app.schemas import InboundIntegrationEvent, SalesReply
from app.services.inbound_integrations import InboundIntegrationService
from app.services.repository import NotFoundError

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.post("/inbound-events", response_model=SalesReply)
async def receive_inbound_event(
    payload: InboundIntegrationEvent,
    session: SessionDep,
    workspace: CurrentIntegrationWorkspaceDep,
    settings: SettingsDep,
) -> SalesReply:
    """Accept a normalized provider-neutral inbound integration event."""

    integration_service = InboundIntegrationService(session, settings)

    try:
        result = await integration_service.handle_event(payload, workspace)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lead not found") from exc

    return SalesReply(
        lead_id=payload.lead_id,
        detected_stage=result.detected_stage,
        draft_reply=result.draft_reply,
        approval_id=result.approval_id,
    )
