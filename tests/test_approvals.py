def test_approved_reply_preserves_sales_stage(client):
    workspace_response = client.post(
        "/api/workspaces",
        json={
            "slug": "demo",
            "name": "Demo Company",
        },
    )

    assert workspace_response.status_code == 201
    
    product_response = client.post(
        "/api/products",
        headers={"X-Workspace-Slug": "demo"},
        json={
            "tenant_id": "demo",
            "name": "AI Sales Assistant Starter",
            "description": "AI sales automation with human approval.",
            "price": 99,
            "minimum_price": 99,
            "metadata_json": {
                "billing": "monthly",
            },
        },
    )

    assert product_response.status_code == 201

    lead_response = client.post(
        "/api/leads",
        headers={"X-Workspace-Slug": "demo"},
        json={
            "tenant_id": "demo",
            "full_name": "Sarra Ben Ali",
            "company_name": "Example Commerce",
            "job_title": "Founder",
            "email": "sarra@example.com",
            "source": "manual",
            "notes": "Needs faster sales follow-up.",
        },
    )

    assert lead_response.status_code == 201
    lead_id = lead_response.json()["id"]

    reply_response = client.post(
    f"/api/conversations/{lead_id}/reply",
    headers={
        "X-Workspace-Slug": "demo",
    },
    json={
        "content": "What is the exact monthly price?",
        "channel": "console",
    },
)

    assert reply_response.status_code == 200

    reply_data = reply_response.json()

    assert reply_data["detected_stage"] == "qualification"
    assert "99.00 monthly" in reply_data["draft_reply"]
    assert reply_data["approval_id"] is not None

    approval_id = reply_data["approval_id"]

    approval_response = client.post(
        f"/api/approvals/{approval_id}/approve",
        headers={
            "X-Workspace-Slug": "demo",
        },
        json={
            "reviewer_note": "Approved by automated test",
        },
    )

    assert approval_response.status_code == 200

    approval_data = approval_response.json()

    assert approval_data["status"] == "executed"
    assert approval_data["payload"]["stage"] == "qualification"

    history_response = client.get(
    f"/api/conversations/{lead_id}",
    headers={
        "X-Workspace-Slug": "demo",
    },
)

    assert history_response.status_code == 200

    messages = history_response.json()

    assert len(messages) == 2

    assert messages[0]["direction"] == "inbound"
    assert messages[0]["stage"] == "qualification"

    assert messages[1]["direction"] == "outbound"
    assert messages[1]["stage"] == "qualification"
    assert "99.00 monthly" in messages[1]["content"]
