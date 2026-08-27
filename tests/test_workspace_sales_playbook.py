from copy import deepcopy
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlmodel import select

from app.config import get_settings
from app.db import get_session
from app.departments.sales.playbook import (
    MAX_ICP_CRITERIA,
    MAX_SALES_PLAYBOOK_BYTES,
    SALES_PLAYBOOK_CRITERION_REGISTRY,
    SalesPlaybookCriterionOperator,
    SalesPlaybookCriterionType,
    SalesPlaybookV1,
)
from app.main import app
from app.models import Lead, Workspace, WorkspaceMember, WorkspaceMemberRole
from app.services.authentication import AuthenticationService
from app.services.sales_playbooks import (
    WorkspaceSalesPlaybookPersistenceError,
    WorkspaceSalesPlaybookService,
)

TEST_PASSWORD = "correct-password"


def _headers(slug: str, token: str | None = None) -> dict[str, str]:
    headers = {"X-Workspace-Slug": slug}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _create_workspace(client, slug: str) -> dict:
    response = client.post(
        "/api/workspaces",
        json={"slug": slug, "name": slug.replace("-", " ").title()},
    )
    assert response.status_code == 201
    return response.json()


def _minimal_playbook() -> dict:
    return {
        "schema_version": 1,
        "icp": {
            "criteria": [
                {
                    "key": "target_industry",
                    "criterion_type": "industry",
                    "operator": "in",
                    "values": [" B2B SaaS ", "Professional Services"],
                    "importance": "required",
                },
                {
                    "key": "minimum_company_size",
                    "criterion_type": "company_size",
                    "operator": "gte",
                    "values": [10],
                    "importance": "preferred",
                },
            ],
            "disqualifiers": [
                {
                    "key": "consumer_only",
                    "criterion_type": "customer_type",
                    "operator": "equals",
                    "values": ["consumer_only"],
                }
            ],
        },
        "qualification": {
            "required_information": [
                {
                    "key": "business_need",
                    "description": "A confirmed business problem relevant to the offering",
                }
            ]
        },
    }


def _validate(value: dict) -> SalesPlaybookV1:
    return SalesPlaybookV1.model_validate(value)


def _criterion(**overrides) -> dict:
    value = {
        "key": "target_industry",
        "criterion_type": "industry",
        "operator": "equals",
        "values": ["software"],
        "importance": "required",
    }
    value.update(overrides)
    return value


def _playbook_with_criteria(criteria: list[dict], *, disqualifiers=None) -> dict:
    return {
        "schema_version": 1,
        "icp": {
            "criteria": criteria,
            "disqualifiers": disqualifiers or [],
        },
        "qualification": {"required_information": []},
    }


def test_valid_minimal_v1_playbook_is_immutable_and_normalized() -> None:
    playbook = _validate(_minimal_playbook())

    assert playbook.schema_version == 1
    assert playbook.icp.criteria[0].values == ("b2b saas", "professional services")
    assert playbook.icp.criteria[1].values == (10,)
    with pytest.raises(ValidationError):
        playbook.icp.criteria[0].key = "changed"  # type: ignore[misc]


def test_registry_contains_only_the_bounded_v1_taxonomy_and_operator_sets() -> None:
    assert set(SALES_PLAYBOOK_CRITERION_REGISTRY) == set(SalesPlaybookCriterionType)
    for criterion_type, specification in SALES_PLAYBOOK_CRITERION_REGISTRY.items():
        if criterion_type in {
            SalesPlaybookCriterionType.COMPANY_SIZE,
            SalesPlaybookCriterionType.CHANNEL_VOLUME,
        }:
            assert specification.operators == {
                SalesPlaybookCriterionOperator.EQUALS,
                SalesPlaybookCriterionOperator.GTE,
                SalesPlaybookCriterionOperator.LTE,
            }
        else:
            assert specification.operators == {
                SalesPlaybookCriterionOperator.EQUALS,
                SalesPlaybookCriterionOperator.IN,
            }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(schema_version=2),
        lambda value: value.update(unexpected=True),
        lambda value: value["icp"].update(unexpected=True),
        lambda value: value["qualification"].update(unexpected=True),
    ],
)
def test_wrong_version_and_unknown_fields_are_rejected(mutation) -> None:
    value = _minimal_playbook()
    mutation(value)
    with pytest.raises(ValidationError):
        _validate(value)


@pytest.mark.parametrize(
    ("criterion_type", "operator"),
    [
        ("unknown_type", "equals"),
        ("industry", "regex"),
        ("industry", "gte"),
        ("company_size", "in"),
    ],
)
def test_unknown_or_incompatible_type_operator_is_rejected(
    criterion_type: str,
    operator: str,
) -> None:
    value = _playbook_with_criteria(
        [_criterion(criterion_type=criterion_type, operator=operator)]
    )
    with pytest.raises(ValidationError):
        _validate(value)


@pytest.mark.parametrize("values", [["10"], [True], [-1], [float("inf")]])
def test_numeric_criteria_require_finite_non_negative_numbers(values) -> None:
    value = _playbook_with_criteria(
        [
            _criterion(
                criterion_type="company_size",
                operator="gte",
                values=values,
            )
        ]
    )
    with pytest.raises(ValidationError):
        _validate(value)


@pytest.mark.parametrize("values", [[], [10], [""], ["x" * 201]])
def test_categorical_values_are_typed_non_empty_and_bounded(values) -> None:
    with pytest.raises(ValidationError):
        _validate(_playbook_with_criteria([_criterion(values=values)]))


def test_non_in_operators_require_one_value_and_normalized_duplicates_fail() -> None:
    with pytest.raises(ValidationError):
        _validate(_playbook_with_criteria([_criterion(values=["a", "b"])]))
    with pytest.raises(ValidationError):
        _validate(
            _playbook_with_criteria(
                [_criterion(operator="in", values=[" B2B SaaS ", "b2b saas"])]
            )
        )


@pytest.mark.parametrize(
    "criteria",
    [
        [_criterion(), _criterion()],
        [_criterion(), _criterion(key="another_industry")],
    ],
)
def test_duplicate_criterion_keys_or_equivalent_rules_are_rejected(criteria) -> None:
    with pytest.raises(ValidationError):
        _validate(_playbook_with_criteria(criteria))


def test_duplicate_disqualifier_keys_and_cross_rule_conflicts_are_rejected() -> None:
    disqualifier = {
        "key": "excluded_industry",
        "criterion_type": "industry",
        "operator": "equals",
        "values": ["software"],
    }
    duplicate_key = {**disqualifier, "values": ["retail"]}
    with pytest.raises(ValidationError):
        _validate(
            _playbook_with_criteria([], disqualifiers=[disqualifier, duplicate_key])
        )

    colliding_key = {**disqualifier, "key": "target_industry"}
    with pytest.raises(ValidationError):
        _validate(_playbook_with_criteria([_criterion()], disqualifiers=[colliding_key]))

    with pytest.raises(ValidationError):
        _validate(_playbook_with_criteria([_criterion()], disqualifiers=[disqualifier]))


def test_required_information_requires_unique_safe_keys_and_bounded_description() -> None:
    value = _minimal_playbook()
    value["qualification"]["required_information"] = [
        {"key": "Business Need", "description": "Known need"}
    ]
    with pytest.raises(ValidationError):
        _validate(value)

    value = _minimal_playbook()
    value["qualification"]["required_information"] *= 2
    with pytest.raises(ValidationError):
        _validate(value)

    value = _minimal_playbook()
    value["qualification"]["required_information"][0]["description"] = "   "
    with pytest.raises(ValidationError):
        _validate(value)


def test_unsupported_importance_and_customer_expression_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _validate(
            _playbook_with_criteria([_criterion(importance="critical")])
        )
    with pytest.raises(ValidationError):
        _validate(
            _playbook_with_criteria(
                [_criterion(expression="lead.company_size > 10")]
            )
        )


def test_criterion_count_and_total_serialized_size_are_bounded() -> None:
    too_many = [
        _criterion(key=f"criterion_{index}", values=[f"value_{index}"])
        for index in range(MAX_ICP_CRITERIA + 1)
    ]
    with pytest.raises(ValidationError):
        _validate(_playbook_with_criteria(too_many))

    oversized = [
        _criterion(
            key=f"criterion_{index}",
            operator="in",
            values=[f"{index}_{value}_" + "x" * 180 for value in range(20)],
        )
        for index in range(MAX_ICP_CRITERIA)
    ]
    serialized_size = len(str(_playbook_with_criteria(oversized)).encode())
    assert serialized_size > MAX_SALES_PLAYBOOK_BYTES
    with pytest.raises(ValidationError):
        _validate(_playbook_with_criteria(oversized))


def test_unconfigured_state_and_malformed_persisted_json_fail_closed(client) -> None:
    workspace_data = _create_workspace(client, "playbook-malformed")
    assert client.get(
        "/api/workspaces/sales-playbook",
        headers=_headers("playbook-malformed"),
    ).json() == {"sales_playbook": None}

    with next(app.dependency_overrides[get_session]()) as session:
        workspace = session.get(Workspace, UUID(workspace_data["id"]))
        assert workspace is not None
        workspace.sales_playbook = {"schema_version": 99}
        session.add(workspace)
        session.commit()
        with pytest.raises(WorkspaceSalesPlaybookPersistenceError):
            WorkspaceSalesPlaybookService(session).read(workspace)

    response = client.get(
        "/api/workspaces/sales-playbook",
        headers=_headers("playbook-malformed"),
    )
    assert response.status_code == 500
    assert response.json() == {"detail": "Stored Sales Playbook is invalid"}


def test_owner_can_replace_and_read_playbook_without_generic_workspace_exposure(client) -> None:
    _create_workspace(client, "playbook-owner")
    payload = {"sales_playbook": _minimal_playbook()}

    created = client.put(
        "/api/workspaces/sales-playbook",
        headers=_headers("playbook-owner"),
        json=payload,
    )
    assert created.status_code == 200
    assert created.json()["sales_playbook"]["icp"]["criteria"][0]["values"] == [
        "b2b saas",
        "professional services",
    ]
    assert client.get(
        "/api/workspaces/sales-playbook",
        headers=_headers("playbook-owner"),
    ).json() == created.json()

    generic = client.get("/api/workspaces/playbook-owner")
    assert generic.status_code == 200
    assert "sales_playbook" not in generic.json()


def test_put_uses_full_replacement_and_body_cannot_select_workspace(client) -> None:
    _create_workspace(client, "playbook-a")
    workspace_b = _create_workspace(client, "playbook-b")
    first = _minimal_playbook()
    assert client.put(
        "/api/workspaces/sales-playbook",
        headers=_headers("playbook-a"),
        json={"sales_playbook": first},
    ).status_code == 200

    replacement = deepcopy(first)
    replacement["icp"]["criteria"] = [
        _criterion(key="target_country", criterion_type="country", values=["tunisia"])
    ]
    replaced = client.put(
        "/api/workspaces/sales-playbook",
        headers=_headers("playbook-a"),
        json={"sales_playbook": replacement},
    )
    assert replaced.status_code == 200
    keys = [item["key"] for item in replaced.json()["sales_playbook"]["icp"]["criteria"]]
    assert keys == ["target_country"]

    denied = client.put(
        "/api/workspaces/sales-playbook",
        headers=_headers("playbook-a"),
        json={"workspace_id": workspace_b["id"], "sales_playbook": first},
    )
    assert denied.status_code == 422
    assert client.get(
        "/api/workspaces/sales-playbook",
        headers=_headers("playbook-b"),
    ).json() == {"sales_playbook": None}


def _add_user_membership(workspace_id: UUID, role: WorkspaceMemberRole) -> str:
    with next(app.dependency_overrides[get_session]()) as session:
        settings = app.dependency_overrides[get_settings]()
        auth = AuthenticationService(session, settings)
        user = auth.register(
            email=f"{role.value}-{workspace_id}@example.com",
            password=TEST_PASSWORD,
        )
        session.add(
            WorkspaceMember(
                workspace_id=workspace_id,
                user_id=user.id,
                role=role,
            )
        )
        session.commit()
        return auth.issue_access_token(user)


def _new_user_without_membership() -> str:
    with next(app.dependency_overrides[get_session]()) as session:
        settings = app.dependency_overrides[get_settings]()
        auth = AuthenticationService(session, settings)
        user = auth.register(
            email="playbook-outsider@example.com",
            password=TEST_PASSWORD,
        )
        return auth.issue_access_token(user)


def test_admin_can_read_and_write_member_can_read_but_not_write(client) -> None:
    workspace = _create_workspace(client, "playbook-rbac")
    workspace_id = UUID(workspace["id"])
    admin_token = _add_user_membership(workspace_id, WorkspaceMemberRole.ADMIN)
    member_token = _add_user_membership(workspace_id, WorkspaceMemberRole.MEMBER)
    payload = {"sales_playbook": _minimal_playbook()}

    assert client.put(
        "/api/workspaces/sales-playbook",
        headers=_headers("playbook-rbac", admin_token),
        json=payload,
    ).status_code == 200
    assert client.get(
        "/api/workspaces/sales-playbook",
        headers=_headers("playbook-rbac", admin_token),
    ).status_code == 200
    assert client.get(
        "/api/workspaces/sales-playbook",
        headers=_headers("playbook-rbac", member_token),
    ).status_code == 200
    assert client.put(
        "/api/workspaces/sales-playbook",
        headers=_headers("playbook-rbac", member_token),
        json=payload,
    ).status_code == 403


def test_outsider_cannot_read_or_write_another_workspace(client) -> None:
    _create_workspace(client, "playbook-private")
    outsider_token = _new_user_without_membership()
    payload = {"sales_playbook": _minimal_playbook()}

    read = client.get(
        "/api/workspaces/sales-playbook",
        headers=_headers("playbook-private", outsider_token),
    )
    write = client.put(
        "/api/workspaces/sales-playbook",
        headers=_headers("playbook-private", outsider_token),
        json=payload,
    )
    assert read.status_code == write.status_code == 404


def test_playbook_does_not_parse_sales_instructions_or_change_legacy_lead_score(client) -> None:
    workspace = _create_workspace(client, "playbook-legacy")
    instructions = client.put(
        "/api/workspaces/sales-instructions",
        headers=_headers("playbook-legacy"),
        json={"instructions": '{"schema_version":1,"icp":{"criteria":[]}}'},
    )
    assert instructions.status_code == 200
    assert client.get(
        "/api/workspaces/sales-playbook",
        headers=_headers("playbook-legacy"),
    ).json() == {"sales_playbook": None}

    with next(app.dependency_overrides[get_session]()) as session:
        lead = Lead(
            tenant_id="playbook-legacy",
            full_name="Legacy Lead",
            company_name="Legacy Company",
            score=42,
        )
        session.add(lead)
        session.commit()
        lead_id = lead.id

    assert client.put(
        "/api/workspaces/sales-playbook",
        headers=_headers("playbook-legacy"),
        json={"sales_playbook": _minimal_playbook()},
    ).status_code == 200

    with next(app.dependency_overrides[get_session]()) as session:
        lead = session.exec(select(Lead).where(Lead.id == lead_id)).one()
        stored_workspace = session.get(Workspace, UUID(workspace["id"]))
        assert lead.score == 42
        assert stored_workspace is not None
        assert stored_workspace.sales_instructions is not None
        assert stored_workspace.sales_playbook is not None
