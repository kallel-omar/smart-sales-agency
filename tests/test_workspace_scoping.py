def test_lead_and_product_require_existing_workspace(client):
    lead_response = client.post(
        "/api/leads",
        headers={"X-Workspace-Slug": "missing-workspace"},
        json={
            "tenant_id": "missing-workspace",
            "full_name": "Sarra Ben Ali",
            "company_name": "Example Commerce",
            "email": "sarra@example.com",
            "source": "manual",
        },
    )

    assert lead_response.status_code == 404
    assert lead_response.json()["detail"] == "Workspace not found"

    product_response = client.post(
        "/api/products",
        headers={"X-Workspace-Slug": "missing-workspace"},
        json={
            "tenant_id": "missing-workspace",
            "name": "AI Sales Assistant",
            "description": "Sales automation product.",
            "price": 99,
            "minimum_price": 99,
            "metadata_json": {},
        },
    )

    assert product_response.status_code == 404
    assert product_response.json()["detail"] == "Workspace not found"


def test_workspace_slug_is_normalized_for_leads_and_products(client):
    workspace_response = client.post(
        "/api/workspaces",
        json={
            "slug": "demo",
            "name": "Demo Company",
        },
    )

    assert workspace_response.status_code == 201

    lead_response = client.post(
        "/api/leads",
        headers={"X-Workspace-Slug": " Demo "},
        json={
            "tenant_id": "body-is-not-authority",
            "full_name": "Sarra Ben Ali",
            "company_name": "Example Commerce",
            "email": "sarra@example.com",
            "source": "manual",
        },
    )

    assert lead_response.status_code == 201
    assert lead_response.json()["tenant_id"] == "demo"

    product_response = client.post(
        "/api/products",
        headers={"X-Workspace-Slug": " DEMO "},
        json={
            "tenant_id": "body-is-not-authority",
            "name": "AI Sales Assistant",
            "description": "Sales automation product.",
            "price": 99,
            "minimum_price": 99,
            "metadata_json": {},
        },
    )

    assert product_response.status_code == 201
    assert product_response.json()["tenant_id"] == "demo"
