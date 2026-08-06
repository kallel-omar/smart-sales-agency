def create_workspace(client, slug: str, name: str) -> None:
    response = client.post(
        "/api/workspaces",
        json={
            "slug": slug,
            "name": name,
        },
    )

    assert response.status_code == 201


def create_lead(client, workspace_slug: str) -> str:
    response = client.post(
        "/api/leads",
        json={
            "tenant_id": workspace_slug,
            "full_name": "Sarra Ben Ali",
            "company_name": "Example Commerce",
            "email": "sarra@example.com",
            "source": "manual",
            "notes": "Needs faster sales follow-up.",
        },
    )

    assert response.status_code == 201
    return response.json()["id"]


def test_conversations_and_workflows_are_isolated(client):
    create_workspace(client, "company-a", "Company A")
    create_workspace(client, "company-b", "Company B")

    lead_id = create_lead(client, "company-a")

    company_b_history = client.get(
        f"/api/conversations/{lead_id}",
        headers={
            "X-Workspace-Slug": "company-b",
        },
    )

    assert company_b_history.status_code == 404
    assert company_b_history.json()["detail"] == "Lead not found"

    company_b_reply = client.post(
        f"/api/conversations/{lead_id}/reply",
        headers={
            "X-Workspace-Slug": "company-b",
        },
        json={
            "content": "What is the monthly price?",
            "channel": "console",
        },
    )

    assert company_b_reply.status_code == 404
    assert company_b_reply.json()["detail"] == "Lead not found"

    company_b_workflow = client.post(
        f"/api/workflows/{lead_id}/run",
        headers={
            "X-Workspace-Slug": "company-b",
        },
    )

    assert company_b_workflow.status_code == 404
    assert company_b_workflow.json()["detail"] == "Lead not found"

    company_a_history = client.get(
        f"/api/conversations/{lead_id}",
        headers={
            "X-Workspace-Slug": "company-a",
        },
    )

    assert company_a_history.status_code == 200
    assert company_a_history.json() == []


def test_workspace_header_is_required_for_operations(client):
    missing_lead_id = "3fa85f64-5717-4562-b3fc-2c963f66afa6"

    history_response = client.get(
        f"/api/conversations/{missing_lead_id}",
    )

    reply_response = client.post(
        f"/api/conversations/{missing_lead_id}/reply",
        json={
            "content": "Hello",
            "channel": "console",
        },
    )

    workflow_response = client.post(
        f"/api/workflows/{missing_lead_id}/run",
    )

    assert history_response.status_code == 422
    assert reply_response.status_code == 422
    assert workflow_response.status_code == 422