import hmac
import json
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import (
    Capability,
    Contact,
    ConversationMessage,
    Department,
    InboundExternalIdentity,
    InboundIntegrationEventReceipt,
    IntegrationAccount,
    Lead,
    OutboundIntegrationAction,
    WorkItem,
    Workspace,
)
from app.services.lead_capture import LeadCaptureService
from app.services.meta_inbound import (
    InboundExternalIdentityBindingError,
    InboundExternalIdentityService,
)

ENDPOINT = "/api/integrations/inbound-events/meta"
META_SECRET_REFERENCE = "INTEGRATION_SECRET_META_TEST"
META_SECRET = "test-meta-app-secret"


def _workspace(client, slug: str) -> None:
    response = client.post(
        "/api/workspaces", json={"slug": slug, "name": slug.replace("-", " ")}
    )
    assert response.status_code == 201


def _account(
    client,
    workspace_slug: str,
    *,
    provider: str,
    external_account_id: str,
) -> dict:
    response = client.post(
        "/api/integrations/accounts",
        headers={"X-Workspace-Slug": workspace_slug},
        json={
            "provider": provider,
            "external_account_id": external_account_id,
            "secret_reference": META_SECRET_REFERENCE,
        },
    )
    assert response.status_code == 201
    return response.json()


def _facebook_message(
    account_id: str,
    *,
    sender_id: str = "fb-user-1",
    event_id: str = "m_fb_1",
    text: str = "Can you tell me the price?",
) -> dict:
    return {
        "object": "page",
        "entry": [
            {
                "id": account_id,
                "time": 1_720_000_000,
                "messaging": [
                    {
                        "sender": {"id": sender_id, "name": "Sarra"},
                        "recipient": {"id": account_id},
                        "timestamp": 1_720_000_001,
                        "message": {"mid": event_id, "text": text},
                    }
                ],
            }
        ],
    }


def _instagram_message(
    account_id: str,
    *,
    sender_id: str = "ig-user-1",
    event_id: str = "m_ig_1",
) -> dict:
    payload = _facebook_message(
        account_id, sender_id=sender_id, event_id=event_id, text="Is this available?"
    )
    payload["object"] = "instagram"
    return payload


def _facebook_comment(account_id: str, event_id: str = "fb_comment_1") -> dict:
    return {
        "object": "page",
        "entry": [
            {
                "id": account_id,
                "time": 1_720_000_010,
                "changes": [
                    {
                        "field": "feed",
                        "value": {
                            "item": "comment",
                            "verb": "add",
                            "comment_id": event_id,
                            "post_id": "post-1",
                            "parent_id": "parent-1",
                            "message": "Interested",
                            "created_time": 1_720_000_009,
                            "from": {"id": "fb-commenter", "name": "Amina"},
                        },
                    }
                ],
            }
        ],
    }


def _instagram_comment(account_id: str, event_id: str = "ig_comment_1") -> dict:
    return {
        "object": "instagram",
        "entry": [
            {
                "id": account_id,
                "time": 1_720_000_020,
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "id": event_id,
                            "text": "Please share details",
                            "timestamp": 1_720_000_019,
                            "from": {"id": "ig-commenter", "username": "amina"},
                            "media": {"id": "media-1"},
                        },
                    }
                ],
            }
        ],
    }


def _signed(account: dict, payload: dict, *, valid: bool = True) -> tuple[dict, bytes]:
    body = json.dumps(payload, separators=(",", ":")).encode()
    digest = hmac.new(META_SECRET.encode(), body, sha256).hexdigest()
    return (
        {
            "Content-Type": "application/json",
            "X-Hub-Signature-256": f"sha256={digest if valid else '0' * 64}",
        },
        body,
    )


def _post(client, account: dict, payload: dict, *, valid: bool = True):
    headers, body = _signed(account, payload, valid=valid)
    return client.post(f"{ENDPOINT}/{account['id']}", headers=headers, content=body)


@pytest.mark.parametrize(
    ("provider", "account_id", "payload_factory", "channel"),
    [
        ("facebook_messenger", "page-1", _facebook_message, "facebook_messenger"),
        ("instagram_dm", "ig-account-1", _instagram_message, "instagram_dm"),
    ],
)
def test_direct_message_captures_and_reuses_external_identity(
    client, monkeypatch, provider, account_id, payload_factory, channel
):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, f"{provider}-workspace")
    account = _account(
        client,
        f"{provider}-workspace",
        provider=provider,
        external_account_id=account_id,
    )

    first = _post(client, account, payload_factory(account_id))
    second = _post(
        client,
        account,
        payload_factory(account_id, event_id=f"m_{provider}_2"),
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["lead_id"] == second.json()["lead_id"]
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        identities = session.exec(select(InboundExternalIdentity)).all()
        contacts = session.exec(select(Contact)).all()
        leads = session.exec(select(Lead)).all()
        capture_items = session.exec(
            select(WorkItem).where(WorkItem.work_type == "lead_capture")
        ).all()
        messages = session.exec(select(ConversationMessage)).all()
        assert len(identities) == len(contacts) == len(leads) == 1
        assert identities[0].channel == channel
        assert str(identities[0].lead_id) == first.json()["lead_id"]
        assert len(capture_items) == len(messages) == 2
        assert capture_items[0].input["metadata"] == {
            "account_id": account_id,
            "timestamp": 1_720_000_001,
            "message_type": "text",
        }


@pytest.mark.parametrize(
    ("provider", "account_id", "payload_factory"),
    [
        ("facebook_messenger", "page-duplicate", _facebook_message),
        ("instagram_dm", "ig-duplicate", _instagram_message),
    ],
)
def test_duplicate_direct_event_is_suppressed(
    client, monkeypatch, provider, account_id, payload_factory
):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, f"{provider}-duplicate")
    account = _account(
        client,
        f"{provider}-duplicate",
        provider=provider,
        external_account_id=account_id,
    )
    payload = payload_factory(account_id)

    first = _post(client, account, payload)
    duplicate = _post(client, account, payload)

    assert first.status_code == duplicate.status_code == 200
    assert duplicate.json() == {
        "duplicate": True,
        "correlation_id": first.json()["correlation_id"],
    }
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        assert len(session.exec(select(Lead)).all()) == 1
        assert len(session.exec(select(Contact)).all()) == 1
        assert len(session.exec(select(WorkItem)).all()) == 1
        assert len(session.exec(select(ConversationMessage)).all()) == 1


def test_external_identity_is_scoped_by_workspace_and_integration_account(
    client, monkeypatch
):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    for slug in ("meta-scope-a", "meta-scope-b"):
        _workspace(client, slug)
    account_a1 = _account(
        client,
        "meta-scope-a",
        provider="facebook_messenger",
        external_account_id="page-a1",
    )
    account_a2 = _account(
        client,
        "meta-scope-a",
        provider="facebook_messenger",
        external_account_id="page-a2",
    )
    account_b = _account(
        client,
        "meta-scope-b",
        provider="facebook_messenger",
        external_account_id="page-b",
    )

    responses = [
        _post(client, account_a1, _facebook_message("page-a1", sender_id="same-user")),
        _post(client, account_a2, _facebook_message("page-a2", sender_id="same-user")),
        _post(client, account_b, _facebook_message("page-b", sender_id="same-user")),
    ]

    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()["lead_id"] for response in responses}) == 3
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        identities = session.exec(select(InboundExternalIdentity)).all()
        assert len(identities) == 3
        assert len({identity.integration_account_id for identity in identities}) == 3
        assert len({identity.workspace_id for identity in identities}) == 2


def test_account_reference_mismatch_is_rejected_before_capture(client, monkeypatch):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, "meta-mismatch")
    account = _account(
        client,
        "meta-mismatch",
        provider="facebook_messenger",
        external_account_id="page-trusted",
    )

    response = _post(client, account, _facebook_message("page-forged"))

    assert response.status_code == 404
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        assert session.exec(select(Lead)).all() == []
        assert session.exec(select(InboundIntegrationEventReceipt)).all() == []


def test_invalid_meta_signature_does_not_provision_or_capture(client, monkeypatch):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    session_dependency = app.dependency_overrides[get_session]
    credential = "legacy-meta-credential"
    with next(session_dependency()) as session:
        workspace = Workspace(slug="legacy-meta", name="Legacy Meta")
        session.add(workspace)
        session.commit()
        session.refresh(workspace)
        account = IntegrationAccount(
            workspace_id=workspace.id,
            provider="facebook_messenger",
            external_account_id="legacy-page",
            secret_reference=META_SECRET_REFERENCE,
            credential_hash=sha256(credential.encode()).hexdigest(),
        )
        session.add(account)
        session.commit()
        account_id = account.id
    account_read = {"id": str(account_id), "inbound_credential": credential}

    response = _post(
        client, account_read, _facebook_message("legacy-page"), valid=False
    )

    assert response.status_code == 401
    with next(session_dependency()) as session:
        assert session.exec(select(Department)).all() == []
        assert session.exec(select(Capability)).all() == []
        assert session.exec(select(Contact)).all() == []
        assert session.exec(select(Lead)).all() == []
        assert session.exec(select(WorkItem)).all() == []
        assert session.exec(select(InboundIntegrationEventReceipt)).all() == []


@pytest.mark.parametrize(
    ("provider", "account_id", "payload", "channel"),
    [
        (
            "facebook_messenger",
            "page-comment",
            _facebook_comment("page-comment"),
            "facebook_comment",
        ),
        (
            "instagram_dm",
            "ig-comment-account",
            _instagram_comment("ig-comment-account"),
            "instagram_comment",
        ),
    ],
)
def test_comments_are_normalized_and_deduplicated_without_business_capture(
    client, monkeypatch, provider, account_id, payload, channel
):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, f"{provider}-comments")
    account = _account(
        client,
        f"{provider}-comments",
        provider=provider,
        external_account_id=account_id,
    )

    first = _post(client, account, payload)
    duplicate = _post(client, account, payload)

    assert first.status_code == duplicate.status_code == 200
    assert first.json()["channel"] == channel
    assert first.json()["event_type"] == "comment"
    assert first.json()["content"]
    assert duplicate.json()["duplicate"] is True
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        assert len(session.exec(select(InboundIntegrationEventReceipt)).all()) == 1
        assert session.exec(select(InboundExternalIdentity)).all() == []
        assert session.exec(select(Contact)).all() == []
        assert session.exec(select(Lead)).all() == []
        assert session.exec(select(WorkItem)).all() == []
        assert session.exec(select(ConversationMessage)).all() == []
        assert session.exec(select(OutboundIntegrationAction)).all() == []


def test_capture_failure_releases_meta_receipt_for_successful_retry(
    client, monkeypatch
):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, "meta-retry")
    account = _account(
        client,
        "meta-retry",
        provider="facebook_messenger",
        external_account_id="page-retry",
    )
    payload = _facebook_message("page-retry", event_id="m_retry")
    original_capture = LeadCaptureService.capture
    attempts = 0

    def fail_once(self, workspace_id, signal):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("forced capture failure")
        return original_capture(self, workspace_id, signal)

    monkeypatch.setattr(LeadCaptureService, "capture", fail_once)

    with pytest.raises(RuntimeError, match="forced capture failure"):
        _post(client, account, payload)
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        assert session.exec(select(InboundIntegrationEventReceipt)).all() == []
        assert session.exec(select(Lead)).all() == []
        identities = session.exec(select(InboundExternalIdentity)).all()
        contacts = session.exec(select(Contact)).all()
        assert len(identities) == len(contacts) == 1
        anchored_identity_id = identities[0].id
        anchored_contact_id = contacts[0].id
        assert identities[0].contact_id == anchored_contact_id
        assert identities[0].lead_id is None

    retry = _post(client, account, payload)
    duplicate = _post(client, account, payload)

    assert retry.status_code == duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    with next(session_dependency()) as session:
        assert len(session.exec(select(InboundIntegrationEventReceipt)).all()) == 1
        assert len(session.exec(select(InboundExternalIdentity)).all()) == 1
        assert len(session.exec(select(Lead)).all()) == 1
        assert len(session.exec(select(WorkItem)).all()) == 1
        assert len(session.exec(select(ConversationMessage)).all()) == 1
        identity = session.exec(select(InboundExternalIdentity)).one()
        assert identity.id == anchored_identity_id
        assert identity.contact_id == anchored_contact_id


def test_identity_anchor_exists_before_capture_and_explicit_contact_is_used(
    client, monkeypatch
):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, "meta-identity-first")
    account = _account(
        client,
        "meta-identity-first",
        provider="facebook_messenger",
        external_account_id="page-identity-first",
    )
    original_capture = LeadCaptureService.capture
    observed = {}

    def observe_anchor(self, workspace_id, signal):
        identity = self.session.exec(select(InboundExternalIdentity)).one()
        contact = self.session.get(Contact, identity.contact_id)
        observed.update(
            identity_id=identity.id,
            contact_id=identity.contact_id,
            lead_id=identity.lead_id,
            signal_contact_id=signal.contact_id,
        )
        assert contact is not None and contact.workspace_id == workspace_id
        return original_capture(self, workspace_id, signal)

    monkeypatch.setattr(LeadCaptureService, "capture", observe_anchor)

    response = _post(
        client,
        account,
        _facebook_message("page-identity-first", event_id="m_identity_first"),
    )

    assert response.status_code == 200
    assert observed["lead_id"] is None
    assert observed["signal_contact_id"] == observed["contact_id"]
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        identity = session.get(InboundExternalIdentity, observed["identity_id"])
        assert identity is not None
        assert str(identity.lead_id) == response.json()["lead_id"]


def test_external_identity_anchor_supports_sender_without_display_name(
    client, monkeypatch
):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, "meta-no-display-name")
    account = _account(
        client,
        "meta-no-display-name",
        provider="facebook_messenger",
        external_account_id="page-no-name",
    )
    payload = _facebook_message("page-no-name", event_id="m_no_name")
    payload["entry"][0]["messaging"][0]["sender"].pop("name")

    response = _post(client, account, payload)

    assert response.status_code == 200
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        contact = session.exec(select(Contact)).one()
        identity = session.exec(select(InboundExternalIdentity)).one()
        assert contact.name is None
        assert identity.contact_id == contact.id


def test_post_capture_binding_failure_keeps_receipt_and_repairs_on_later_event(
    client, monkeypatch
):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, "meta-binding-recovery")
    account = _account(
        client,
        "meta-binding-recovery",
        provider="facebook_messenger",
        external_account_id="page-binding-recovery",
    )
    first_payload = _facebook_message(
        "page-binding-recovery", event_id="m_binding_failure"
    )
    original_bind = InboundExternalIdentityService.bind_lead

    def fail_binding(self, workspace, integration_account, identity, lead_id):
        raise InboundExternalIdentityBindingError("forced binding failure")

    monkeypatch.setattr(InboundExternalIdentityService, "bind_lead", fail_binding)

    first = _post(client, account, first_payload)
    duplicate = _post(client, account, first_payload)

    assert first.status_code == duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        identity = session.exec(select(InboundExternalIdentity)).one()
        assert identity.lead_id is None
        anchored_contact_id = identity.contact_id
        assert len(session.exec(select(InboundIntegrationEventReceipt)).all()) == 1
        assert len(session.exec(select(Lead)).all()) == 1
        assert len(session.exec(select(WorkItem)).all()) == 1

    monkeypatch.setattr(InboundExternalIdentityService, "bind_lead", original_bind)
    later = _post(
        client,
        account,
        _facebook_message(
            "page-binding-recovery", event_id="m_binding_recovery_later"
        ),
    )

    assert later.status_code == 200
    assert later.json()["lead_id"] == first.json()["lead_id"]
    with next(session_dependency()) as session:
        identity = session.exec(select(InboundExternalIdentity)).one()
        assert identity.contact_id == anchored_contact_id
        assert str(identity.lead_id) == first.json()["lead_id"]
        assert len(session.exec(select(Contact)).all()) == 1
        assert len(session.exec(select(Lead)).all()) == 1
        assert len(session.exec(select(WorkItem)).all()) == 2


def test_identity_with_multiple_workspace_leads_fails_without_guessing(
    client, monkeypatch
):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, "meta-ambiguous-recovery")
    account = _account(
        client,
        "meta-ambiguous-recovery",
        provider="facebook_messenger",
        external_account_id="page-ambiguous",
    )

    def fail_binding(self, workspace, integration_account, identity, lead_id):
        raise InboundExternalIdentityBindingError("forced binding failure")

    monkeypatch.setattr(InboundExternalIdentityService, "bind_lead", fail_binding)
    first = _post(
        client,
        account,
        _facebook_message("page-ambiguous", event_id="m_ambiguous_first"),
    )
    assert first.status_code == 200

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        identity = session.exec(select(InboundExternalIdentity)).one()
        workspace = session.exec(
            select(Workspace).where(Workspace.slug == "meta-ambiguous-recovery")
        ).one()
        session.add(
            Lead(
                tenant_id=workspace.slug,
                contact_id=identity.contact_id,
                full_name="Another lead",
                company_name="Unknown company",
                source="facebook_messenger",
            )
        )
        session.commit()

    monkeypatch.undo()
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    ambiguous = _post(
        client,
        account,
        _facebook_message("page-ambiguous", event_id="m_ambiguous_second"),
    )

    assert ambiguous.status_code == 409
    assert ambiguous.json()["detail"] == "External identity has multiple linked Leads"
    with next(session_dependency()) as session:
        assert len(session.exec(select(Lead)).all()) == 2
        assert len(session.exec(select(WorkItem)).all()) == 1
        assert len(session.exec(select(InboundIntegrationEventReceipt)).all()) == 1


def test_lead_recovery_ignores_leads_from_another_workspace(client, monkeypatch):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, "meta-recovery-local")
    _workspace(client, "meta-recovery-foreign")
    account = _account(
        client,
        "meta-recovery-local",
        provider="facebook_messenger",
        external_account_id="page-recovery-local",
    )
    payload = _facebook_message("page-recovery-local", event_id="m_recovery_scope")
    original_capture = LeadCaptureService.capture

    def fail_capture(self, workspace_id, signal):
        raise RuntimeError("forced capture failure")

    monkeypatch.setattr(LeadCaptureService, "capture", fail_capture)
    with pytest.raises(RuntimeError, match="forced capture failure"):
        _post(client, account, payload)

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        identity = session.exec(select(InboundExternalIdentity)).one()
        foreign_workspace = session.exec(
            select(Workspace).where(Workspace.slug == "meta-recovery-foreign")
        ).one()
        foreign_lead = Lead(
            tenant_id=foreign_workspace.slug,
            contact_id=identity.contact_id,
            full_name="Foreign lead",
            company_name="Unknown company",
            source="facebook_messenger",
        )
        session.add(foreign_lead)
        session.commit()
        session.refresh(foreign_lead)
        foreign_lead_id = foreign_lead.id

    monkeypatch.setattr(LeadCaptureService, "capture", original_capture)
    retry = _post(client, account, payload)

    assert retry.status_code == 200
    assert UUID(retry.json()["lead_id"]) != foreign_lead_id
    with next(session_dependency()) as session:
        identity = session.exec(select(InboundExternalIdentity)).one()
        local_workspace = session.exec(
            select(Workspace).where(Workspace.slug == "meta-recovery-local")
        ).one()
        local_lead = session.get(Lead, identity.lead_id)
        assert local_lead is not None
        assert local_lead.tenant_id == local_workspace.slug
        assert len(session.exec(select(Contact)).all()) == 1
        assert len(session.exec(select(WorkItem)).all()) == 1


def test_external_identity_unique_scope_is_enforced(client, monkeypatch):
    monkeypatch.setenv(META_SECRET_REFERENCE, META_SECRET)
    _workspace(client, "meta-identity-unique")
    account = _account(
        client,
        "meta-identity-unique",
        provider="facebook_messenger",
        external_account_id="page-identity-unique",
    )
    response = _post(
        client,
        account,
        _facebook_message("page-identity-unique", event_id="m_identity_unique"),
    )
    assert response.status_code == 200

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        identity = session.exec(select(InboundExternalIdentity)).one()
        session.add(
            InboundExternalIdentity(
                workspace_id=identity.workspace_id,
                integration_account_id=identity.integration_account_id,
                channel=identity.channel,
                external_subject_id=identity.external_subject_id,
                contact_id=identity.contact_id,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        assert len(session.exec(select(InboundExternalIdentity)).all()) == 1
