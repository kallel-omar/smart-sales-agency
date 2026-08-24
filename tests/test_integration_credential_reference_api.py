def create_workspace(client, slug: str) -> None:
    response = client.post(
        "/api/workspaces",
        json={"slug": slug, "name": slug.replace("-", " ").title()},
    )
    assert response.status_code == 201


def workspace_headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def provision_account(client, workspace_slug: str) -> dict:
    response = client.post(
        "/api/integrations/accounts",
        headers=workspace_headers(workspace_slug),
        json={
            "provider": "generic_hmac",
            "external_account_id": f"{workspace_slug}-account",
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_credential_reference_api_creates_and_lists_safe_reference(client):
    create_workspace(client, "credential-api-a")
    account = provision_account(client, "credential-api-a")

    created = client.put(
        (
            f"/api/integrations/accounts/{account['id']}"
            "/credential-references/api_access_token"
        ),
        headers=workspace_headers("credential-api-a"),
        json={
            "secret_reference": "INTEGRATION_SECRET_WHATSAPP_API_TOKEN",
        },
    )

    assert created.status_code == 200
    body = created.json()

    assert body["integration_account_id"] == account["id"]
    assert body["workspace_id"] == account["workspace_id"]
    assert body["purpose"] == "api_access_token"

    # Backend secret references must never be exposed through the API.
    assert "secret_reference" not in body
    assert "INTEGRATION_SECRET_WHATSAPP_API_TOKEN" not in str(body)

    listed = client.get(
        f"/api/integrations/accounts/{account['id']}/credential-references",
        headers=workspace_headers("credential-api-a"),
    )

    assert listed.status_code == 200
    references = listed.json()

    assert len(references) == 1
    assert references[0]["id"] == body["id"]
    assert references[0]["purpose"] == "api_access_token"
    assert "secret_reference" not in references[0]
    assert "INTEGRATION_SECRET_WHATSAPP_API_TOKEN" not in str(references)


def test_credential_reference_api_updates_same_purpose_without_duplicate(client):
    create_workspace(client, "credential-api-update")
    account = provision_account(client, "credential-api-update")

    url = (
        f"/api/integrations/accounts/{account['id']}"
        "/credential-references/api_access_token"
    )

    first = client.put(
        url,
        headers=workspace_headers("credential-api-update"),
        json={
            "secret_reference": "INTEGRATION_SECRET_WHATSAPP_API_TOKEN",
        },
    )
    assert first.status_code == 200

    updated = client.put(
        url,
        headers=workspace_headers("credential-api-update"),
        json={
            "secret_reference": "INTEGRATION_SECRET_WHATSAPP_API_TOKEN_ROTATED",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["id"] == first.json()["id"]

    listed = client.get(
        f"/api/integrations/accounts/{account['id']}/credential-references",
        headers=workspace_headers("credential-api-update"),
    )

    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert "INTEGRATION_SECRET_WHATSAPP_API_TOKEN_ROTATED" not in str(
        listed.json()
    )


def test_credential_reference_api_rejects_invalid_configuration(client):
    create_workspace(client, "credential-api-invalid")
    account = provision_account(client, "credential-api-invalid")

    invalid_purpose = client.put(
        (
            f"/api/integrations/accounts/{account['id']}"
            "/credential-references/api-access-token"
        ),
        headers=workspace_headers("credential-api-invalid"),
        json={
            "secret_reference": "INTEGRATION_SECRET_WHATSAPP_API_TOKEN",
        },
    )

    assert invalid_purpose.status_code == 422

    invalid_reference = client.put(
        (
            f"/api/integrations/accounts/{account['id']}"
            "/credential-references/api_access_token"
        ),
        headers=workspace_headers("credential-api-invalid"),
        json={
            "secret_reference": "WHATSAPP_REAL_ACCESS_TOKEN",
        },
    )

    assert invalid_reference.status_code == 422


def test_credential_reference_api_is_workspace_isolated(client):
    create_workspace(client, "credential-api-owner")
    create_workspace(client, "credential-api-other")

    account = provision_account(client, "credential-api-owner")

    denied_write = client.put(
        (
            f"/api/integrations/accounts/{account['id']}"
            "/credential-references/api_access_token"
        ),
        headers=workspace_headers("credential-api-other"),
        json={
            "secret_reference": "INTEGRATION_SECRET_OTHER_WORKSPACE",
        },
    )

    assert denied_write.status_code == 404
    assert denied_write.json()["detail"] == "Integration account not found"

    denied_read = client.get(
        f"/api/integrations/accounts/{account['id']}/credential-references",
        headers=workspace_headers("credential-api-other"),
    )

    assert denied_read.status_code == 404
    assert denied_read.json()["detail"] == "Integration account not found"