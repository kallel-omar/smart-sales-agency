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


def create_pending_approval(client, workspace_slug: str) -> str:
    lead_id = create_lead(client, workspace_slug)

    response = client.post(
        f"/api/conversations/{lead_id}/reply",
        headers={
            "X-Workspace-Slug": workspace_slug,
        },
        json={
            "content": "What is the monthly price?",
            "channel": "console",
        },
    )

    assert response.status_code == 200
    assert response.json()["approval_id"] is not None
    return response.json()["approval_id"]


def approval_status(client, workspace_slug: str, approval_id: str) -> str:
    response = client.get(
        "/api/approvals",
        headers={
            "X-Workspace-Slug": workspace_slug,
        },
    )

    assert response.status_code == 200
    return next(
        approval["status"]
        for approval in response.json()
        if approval["id"] == approval_id
    )


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


def test_approvals_are_isolated_between_workspaces(client):
    create_workspace(client, "company-a", "Company A")
    create_workspace(client, "company-b", "Company B")

    approval_id = create_pending_approval(client, "company-a")

    company_a_approvals = client.get(
        "/api/approvals",
        headers={
            "X-Workspace-Slug": "company-a",
        },
    )

    assert company_a_approvals.status_code == 200
    assert [approval["id"] for approval in company_a_approvals.json()] == [approval_id]

    company_b_approvals = client.get(
        "/api/approvals",
        headers={
            "X-Workspace-Slug": "company-b",
        },
    )

    assert company_b_approvals.status_code == 200
    assert company_b_approvals.json() == []


def test_cross_workspace_approval_is_denied(client):
    create_workspace(client, "company-a", "Company A")
    create_workspace(client, "company-b", "Company B")

    approval_id = create_pending_approval(client, "company-a")

    response = client.post(
        f"/api/approvals/{approval_id}/approve",
        headers={
            "X-Workspace-Slug": "company-b",
        },
        json={
            "reviewer_note": "Should not be accepted",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Approval request not found"
    assert approval_status(client, "company-a", approval_id) == "pending"


def test_cross_workspace_rejection_is_denied(client):
    create_workspace(client, "company-a", "Company A")
    create_workspace(client, "company-b", "Company B")

    approval_id = create_pending_approval(client, "company-a")

    response = client.post(
        f"/api/approvals/{approval_id}/reject",
        headers={
            "X-Workspace-Slug": "company-b",
        },
        json={
            "reviewer_note": "Should not be accepted",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Approval request not found"
    assert approval_status(client, "company-a", approval_id) == "pending"


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
    approvals_response = client.get("/api/approvals")

    assert history_response.status_code == 422
    assert reply_response.status_code == 422
    assert workflow_response.status_code == 422
    assert approvals_response.status_code == 422
