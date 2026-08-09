from tests.test_outbound_action_audit import _headers, _setup
from tests.test_outbound_action_transition_validation import _url


def test_transition_explanation_exposes_safe_terminal_timestamp_only(client):
    account, action = _setup(client, "company-a")
    action_url = f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}"
    assert client.post(f"{action_url}/cancel", headers=_headers("company-a")).status_code == 200

    response = client.get(
        _url(account, action), headers=_headers("company-a"), params={"target": "delivered"}
    )

    assert response.status_code == 200
    detail = response.json()["denial_reason_detail"]
    assert detail["code"] == "action_cancelled"
    assert detail["message"] == "The outbound action was cancelled."
    assert detail["cancelled_at"] is not None
    assert detail["delivered_at"] is None
    assert detail["expired_at"] is None
    for field in ("content", "payload", "idempotency_key", "credential_hash", "secret_reference"):
        assert field not in detail
