"""add channel connection lifecycle foundation

Revision ID: 20260826_014
Revises: 20260825_013
Create Date: 2026-08-26 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260826_014"
down_revision: str | None = "20260825_013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVE_IDENTITY_INDEX = "uq_active_meta_whatsapp_external_account"
ACTIVE_IDENTITY_PROVIDERS = (
    "whatsapp_cloud",
    "facebook_messenger",
    "instagram_dm",
)


def _require_no_active_identity_conflicts(bind) -> None:
    placeholders = ", ".join(f"'{provider}'" for provider in ACTIVE_IDENTITY_PROVIDERS)
    active_predicate = "active IS TRUE" if bind.dialect.name == "postgresql" else "active = 1"
    conflict = bind.execute(
        sa.text(
            "SELECT 1 FROM integrationaccount "
            f"WHERE provider IN ({placeholders}) "
            "AND external_account_id IS NOT NULL "
            f"AND {active_predicate} "
            "GROUP BY provider, external_account_id HAVING COUNT(*) > 1"
        )
    ).first()
    if conflict is not None:
        raise RuntimeError(
            "Active provider identity ownership conflicts must be resolved before migration"
        )


def upgrade() -> None:
    bind = op.get_bind()
    _require_no_active_identity_conflicts(bind)
    account_columns = {
        column["name"] for column in sa.inspect(bind).get_columns("integrationaccount")
    }
    account_column_definitions = {
        "connection_status": sa.Column(
            "connection_status",
            sa.String(length=50),
            nullable=False,
            server_default="configured",
        ),
        "last_validated_at": sa.Column(
            "last_validated_at", sa.DateTime(timezone=True), nullable=True
        ),
        "reconnect_required_at": sa.Column(
            "reconnect_required_at", sa.DateTime(timezone=True), nullable=True
        ),
        "last_connection_error_code": sa.Column(
            "last_connection_error_code", sa.String(length=100), nullable=True
        ),
    }
    for name, column in account_column_definitions.items():
        if name not in account_columns:
            op.add_column("integrationaccount", column)

    account_indexes = {
        index["name"] for index in sa.inspect(bind).get_indexes("integrationaccount")
    }
    for index_name, column_name in (
        (op.f("ix_integrationaccount_connection_status"), "connection_status"),
        (op.f("ix_integrationaccount_last_validated_at"), "last_validated_at"),
        (op.f("ix_integrationaccount_reconnect_required_at"), "reconnect_required_at"),
    ):
        if index_name not in account_indexes:
            op.create_index(
                index_name,
                "integrationaccount",
                [column_name],
                unique=False,
            )

    predicate = (
        sa.text(
            "provider IN ('whatsapp_cloud', 'facebook_messenger', 'instagram_dm') "
            "AND external_account_id IS NOT NULL AND active IS TRUE"
        )
        if bind.dialect.name == "postgresql"
        else sa.text(
            "provider IN ('whatsapp_cloud', 'facebook_messenger', 'instagram_dm') "
            "AND external_account_id IS NOT NULL AND active = 1"
        )
    )
    dialect_options = (
        {"postgresql_where": predicate}
        if bind.dialect.name == "postgresql"
        else {"sqlite_where": predicate}
    )
    if ACTIVE_IDENTITY_INDEX not in account_indexes:
        op.create_index(
            ACTIVE_IDENTITY_INDEX,
            "integrationaccount",
            ["provider", "external_account_id"],
            unique=True,
            **dialect_options,
        )

    credential_columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("integrationcredentialreference")
    }
    if "expires_at" not in credential_columns:
        op.add_column(
            "integrationcredentialreference",
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        )
    credential_indexes = {
        index["name"]
        for index in sa.inspect(bind).get_indexes("integrationcredentialreference")
    }
    expires_index = op.f("ix_integrationcredentialreference_expires_at")
    if expires_index not in credential_indexes:
        op.create_index(
            expires_index,
            "integrationcredentialreference",
            ["expires_at"],
            unique=False,
        )

    audit_inspector = sa.inspect(bind)
    audit_columns = {
        column["name"]
        for column in audit_inspector.get_columns("integrationaccountauditevent")
    }
    audit_indexes = {
        index["name"]
        for index in audit_inspector.get_indexes("integrationaccountauditevent")
    }
    actor_added = "actor_user_id" not in audit_columns
    actor_fk_present = any(
        foreign_key.get("constrained_columns") == ["actor_user_id"]
        for foreign_key in audit_inspector.get_foreign_keys(
            "integrationaccountauditevent"
        )
    )
    actor_index = op.f("ix_integrationaccountauditevent_actor_user_id")
    with op.batch_alter_table("integrationaccountauditevent") as batch_op:
        if actor_added:
            batch_op.add_column(sa.Column("actor_user_id", sa.Uuid(), nullable=True))
        if "credential_purpose" not in audit_columns:
            batch_op.add_column(
                sa.Column("credential_purpose", sa.String(length=100), nullable=True)
            )
        if "reason_code" not in audit_columns:
            batch_op.add_column(
                sa.Column("reason_code", sa.String(length=100), nullable=True)
            )
        if not actor_fk_present:
            batch_op.create_foreign_key(
                "fk_integration_account_audit_actor_user",
                "platform_user",
                ["actor_user_id"],
                ["id"],
            )
        if actor_index not in audit_indexes:
            batch_op.create_index(actor_index, ["actor_user_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    audit_inspector = sa.inspect(bind)
    audit_columns = {
        column["name"]
        for column in audit_inspector.get_columns("integrationaccountauditevent")
    }
    audit_indexes = {
        index["name"]
        for index in audit_inspector.get_indexes("integrationaccountauditevent")
    }
    actor_index = op.f("ix_integrationaccountauditevent_actor_user_id")
    with op.batch_alter_table("integrationaccountauditevent") as batch_op:
        if actor_index in audit_indexes:
            batch_op.drop_index(actor_index)
        for column_name in ("reason_code", "credential_purpose", "actor_user_id"):
            if column_name in audit_columns:
                batch_op.drop_column(column_name)

    credential_columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("integrationcredentialreference")
    }
    credential_indexes = {
        index["name"]
        for index in sa.inspect(bind).get_indexes("integrationcredentialreference")
    }
    expires_index = op.f("ix_integrationcredentialreference_expires_at")
    if expires_index in credential_indexes:
        op.drop_index(expires_index, table_name="integrationcredentialreference")
    if "expires_at" in credential_columns:
        op.drop_column("integrationcredentialreference", "expires_at")

    account_inspector = sa.inspect(bind)
    account_columns = {
        column["name"]
        for column in account_inspector.get_columns("integrationaccount")
    }
    account_indexes = {
        index["name"]
        for index in account_inspector.get_indexes("integrationaccount")
    }
    for index_name in (
        ACTIVE_IDENTITY_INDEX,
        op.f("ix_integrationaccount_reconnect_required_at"),
        op.f("ix_integrationaccount_last_validated_at"),
        op.f("ix_integrationaccount_connection_status"),
    ):
        if index_name in account_indexes:
            op.drop_index(index_name, table_name="integrationaccount")
    for column_name in (
        "last_connection_error_code",
        "reconnect_required_at",
        "last_validated_at",
        "connection_status",
    ):
        if column_name in account_columns:
            op.drop_column("integrationaccount", column_name)
