"""add inbound external identities

Revision ID: 20260820_008
Revises: 20260820_007
Create Date: 2026-08-20 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260820_008"
down_revision: str | None = "20260820_007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "inboundexternalidentity" in inspector.get_table_names():
        return
    op.create_table(
        "inboundexternalidentity",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("integration_account_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("external_subject_id", sa.String(length=255), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.ForeignKeyConstraint(["integration_account_id"], ["integrationaccount.id"]),
        sa.ForeignKeyConstraint(["contact_id"], ["contact.id"]),
        sa.ForeignKeyConstraint(["lead_id"], ["lead.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "integration_account_id",
            "channel",
            "external_subject_id",
            name="uq_inbound_external_identity_subject",
        ),
    )
    op.create_index(
        op.f("ix_inboundexternalidentity_workspace_id"),
        "inboundexternalidentity",
        ["workspace_id"],
    )
    op.create_index(
        op.f("ix_inboundexternalidentity_integration_account_id"),
        "inboundexternalidentity",
        ["integration_account_id"],
    )
    op.create_index(
        op.f("ix_inboundexternalidentity_channel"),
        "inboundexternalidentity",
        ["channel"],
    )
    op.create_index(
        op.f("ix_inboundexternalidentity_contact_id"),
        "inboundexternalidentity",
        ["contact_id"],
    )
    op.create_index(
        op.f("ix_inboundexternalidentity_lead_id"),
        "inboundexternalidentity",
        ["lead_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "inboundexternalidentity" not in sa.inspect(bind).get_table_names():
        return
    op.drop_index(
        op.f("ix_inboundexternalidentity_lead_id"),
        table_name="inboundexternalidentity",
    )
    op.drop_index(
        op.f("ix_inboundexternalidentity_contact_id"),
        table_name="inboundexternalidentity",
    )
    op.drop_index(
        op.f("ix_inboundexternalidentity_channel"),
        table_name="inboundexternalidentity",
    )
    op.drop_index(
        op.f("ix_inboundexternalidentity_integration_account_id"),
        table_name="inboundexternalidentity",
    )
    op.drop_index(
        op.f("ix_inboundexternalidentity_workspace_id"),
        table_name="inboundexternalidentity",
    )
    op.drop_table("inboundexternalidentity")
