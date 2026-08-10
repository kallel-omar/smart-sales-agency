def create_workspace(client, slug: str, name: str) -> None:
    response = client.post(
        "/api/workspaces",
        json={
            "slug": slug,
            "name": name,
        },
    )

    assert response.status_code == 201


def test_leads_are_isolated_between_workspaces(client):
    create_workspace(client, "company-a", "Company A")
    create_workspace(client, "company-b", "Company B")

    lead_a_response = client.post(
        "/api/leads",
        headers={"X-Workspace-Slug": "company-a"},
        json={
            "tenant_id": "company-a",
            "full_name": "Lead A",
            "company_name": "Company A Customer",
            "email": "lead-a@example.com",
            "source": "manual",
        },
    )

    assert lead_a_response.status_code == 201
    lead_a_id = lead_a_response.json()["id"]

    lead_b_response = client.post(
        "/api/leads",
        headers={"X-Workspace-Slug": "company-b"},
        json={
            "tenant_id": "company-b",
            "full_name": "Lead B",
            "company_name": "Company B Customer",
            "email": "lead-b@example.com",
            "source": "manual",
        },
    )

    assert lead_b_response.status_code == 201

    company_a_leads = client.get(
        "/api/leads",
        headers={
            "X-Workspace-Slug": "company-a",
        },
    )

    assert company_a_leads.status_code == 200

    company_a_data = company_a_leads.json()

    assert len(company_a_data) == 1
    assert company_a_data[0]["full_name"] == "Lead A"
    assert company_a_data[0]["tenant_id"] == "company-a"

    hidden_lead_response = client.get(
        f"/api/leads/{lead_a_id}",
        headers={
            "X-Workspace-Slug": "company-b",
        },
    )

    assert hidden_lead_response.status_code == 404
    assert hidden_lead_response.json()["detail"] == "Lead not found"


def test_products_are_isolated_between_workspaces(client):
    create_workspace(client, "company-a", "Company A")
    create_workspace(client, "company-b", "Company B")

    product_a_response = client.post(
        "/api/products",
        headers={"X-Workspace-Slug": "company-a"},
        json={
            "tenant_id": "company-a",
            "name": "Company A Product",
            "description": "Product belonging to Company A.",
            "price": 99,
            "minimum_price": 80,
            "metadata_json": {},
        },
    )

    assert product_a_response.status_code == 201

    product_b_response = client.post(
        "/api/products",
        headers={"X-Workspace-Slug": "company-b"},
        json={
            "tenant_id": "company-b",
            "name": "Company B Product",
            "description": "Product belonging to Company B.",
            "price": 149,
            "minimum_price": 120,
            "metadata_json": {},
        },
    )

    assert product_b_response.status_code == 201

    company_a_products = client.get(
        "/api/products",
        headers={
            "X-Workspace-Slug": "company-a",
        },
    )

    assert company_a_products.status_code == 200

    company_a_data = company_a_products.json()

    assert len(company_a_data) == 1
    assert company_a_data[0]["name"] == "Company A Product"
    assert company_a_data[0]["tenant_id"] == "company-a"


def test_workspace_header_is_required_for_protected_reads(client):
    leads_response = client.get("/api/leads")
    products_response = client.get("/api/products")

    assert leads_response.status_code == 422
    assert products_response.status_code == 422
