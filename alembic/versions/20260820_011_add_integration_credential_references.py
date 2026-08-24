"""add integration credential references

Revision ID: 20260820_011
Revises: 20260820_010
Create Date: 2026-08-22 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260820_011"
down_revision: str | None = "20260820_010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "integrationcredentialreference" in inspector.get_table_names():
        return

    op.create_table(
        "integrationcredentialreference",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("integration_account_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=100), nullable=False),
        sa.Column("secret_reference", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
        ),
        sa.ForeignKeyConstraint(
            ["integration_account_id"],
            ["integrationaccount.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "integration_account_id",
            "purpose",
            name="uq_integration_credential_reference_purpose",
        ),
    )

    op.create_index(
        op.f("ix_integrationcredentialreference_workspace_id"),
        "integrationcredentialreference",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_integrationcredentialreference_integration_account_id"),
        "integrationcredentialreference",
        ["integration_account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_integrationcredentialreference_purpose"),
        "integrationcredentialreference",
        ["purpose"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "integrationcredentialreference" not in inspector.get_table_names():
        return

    op.drop_index(
        op.f("ix_integrationcredentialreference_purpose"),
        table_name="integrationcredentialreference",
    )
    op.drop_index(
        op.f("ix_integrationcredentialreference_integration_account_id"),
        table_name="integrationcredentialreference",
    )
    op.drop_index(
        op.f("ix_integrationcredentialreference_workspace_id"),
        table_name="integrationcredentialreference",
    )
    op.drop_table("integrationcredentialreference")