def test_workspace_creation_and_retrieval(client):
    create_response = client.post(
        "/api/workspaces",
        json={
            "slug": "demo-company",
            "name": "Demo Company",
        },
    )

    assert create_response.status_code == 201

    workspace = create_response.json()

    assert workspace["slug"] == "demo-company"
    assert workspace["name"] == "Demo Company"
    assert workspace["active"] is True

    get_response = client.get(
        "/api/workspaces/demo-company",
    )

    assert get_response.status_code == 200
    assert get_response.json()["id"] == workspace["id"]


def test_duplicate_workspace_slug_is_rejected(client):
    payload = {
        "slug": "demo-company",
        "name": "Demo Company",
    }

    first_response = client.post(
        "/api/workspaces",
        json=payload,
    )

    assert first_response.status_code == 201

    duplicate_response = client.post(
        "/api/workspaces",
        json=payload,
    )

    assert duplicate_response.status_code == 409
    assert (
        duplicate_response.json()["detail"]
        == "A workspace with this slug already exists"
    )