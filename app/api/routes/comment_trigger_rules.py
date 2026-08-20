from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import (
    CurrentWorkspaceDep,
    IntegrationManagePermissionDep,
    SessionDep,
)
from app.schemas import (
    InboundCommentTriggerRuleCreate,
    InboundCommentTriggerRuleRead,
    InboundCommentTriggerRuleUpdate,
)
from app.services.comment_trigger_rules import (
    CommentTriggerRuleNotFoundError,
    CommentTriggerRuleService,
    CommentTriggerRuleValidationError,
    DuplicateCommentTriggerRuleError,
)

router = APIRouter(prefix="/integrations/comment-trigger-rules", tags=["integrations"])


def _read(rule) -> InboundCommentTriggerRuleRead:
    return InboundCommentTriggerRuleRead.model_validate(rule)


@router.post("", response_model=InboundCommentTriggerRuleRead, status_code=status.HTTP_201_CREATED)
def create_comment_trigger_rule(
    payload: InboundCommentTriggerRuleCreate,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: IntegrationManagePermissionDep,
) -> InboundCommentTriggerRuleRead:
    try:
        rule = CommentTriggerRuleService(session).create(workspace, **payload.model_dump())
    except CommentTriggerRuleValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DuplicateCommentTriggerRuleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _read(rule)


@router.get("", response_model=list[InboundCommentTriggerRuleRead])
def list_comment_trigger_rules(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: IntegrationManagePermissionDep,
) -> list[InboundCommentTriggerRuleRead]:
    return [
        _read(rule) for rule in CommentTriggerRuleService(session).list_for_workspace(workspace)
    ]


@router.patch("/{rule_id}", response_model=InboundCommentTriggerRuleRead)
def update_comment_trigger_rule(
    rule_id: UUID,
    payload: InboundCommentTriggerRuleUpdate,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: IntegrationManagePermissionDep,
) -> InboundCommentTriggerRuleRead:
    try:
        rule = CommentTriggerRuleService(session).update(
            workspace,
            rule_id,
            values=payload.model_dump(exclude_unset=True),
        )
    except CommentTriggerRuleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CommentTriggerRuleValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DuplicateCommentTriggerRuleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _read(rule)


@router.post("/{rule_id}/enable", response_model=InboundCommentTriggerRuleRead)
def enable_comment_trigger_rule(
    rule_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: IntegrationManagePermissionDep,
) -> InboundCommentTriggerRuleRead:
    return _set_enabled(session, workspace, rule_id, True)


@router.post("/{rule_id}/disable", response_model=InboundCommentTriggerRuleRead)
def disable_comment_trigger_rule(
    rule_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: IntegrationManagePermissionDep,
) -> InboundCommentTriggerRuleRead:
    return _set_enabled(session, workspace, rule_id, False)


def _set_enabled(session, workspace, rule_id: UUID, enabled: bool):
    try:
        return _read(CommentTriggerRuleService(session).set_enabled(workspace, rule_id, enabled))
    except CommentTriggerRuleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
