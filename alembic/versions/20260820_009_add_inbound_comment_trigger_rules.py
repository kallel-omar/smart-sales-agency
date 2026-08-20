"""add inbound comment trigger rules

Revision ID: 20260820_009
Revises: 20260820_008
Create Date: 2026-08-20 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260820_009"
down_revision: str | None = "20260820_008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "inboundcommenttriggerrule" in inspector.get_table_names():
        return
    op.create_table(
        "inboundcommenttriggerrule",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("integration_account_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("keywords", sa.JSON(), nullable=False),
        sa.Column("content_external_id", sa.String(length=255), nullable=True),
        sa.Column("dm_message", sa.Text(), nullable=False),
        sa.Column("send_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["integration_account_id"], ["integrationaccount.id"]),
        sa.ForeignKeyConstraint(["send_assignment_id"], ["ai_employee_capability_assignment.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "integration_account_id",
            "name",
            name="uq_inbound_comment_trigger_rule_name",
        ),
    )
    for column in (
        "workspace_id",
        "integration_account_id",
        "channel",
        "enabled",
        "content_external_id",
        "send_assignment_id",
    ):
        op.create_index(
            op.f(f"ix_inboundcommenttriggerrule_{column}"),
            "inboundcommenttriggerrule",
            [column],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "inboundcommenttriggerrule" not in sa.inspect(bind).get_table_names():
        return
    for column in reversed(
        (
            "workspace_id",
            "integration_account_id",
            "channel",
            "enabled",
            "content_external_id",
            "send_assignment_id",
        )
    ):
        op.drop_index(
            op.f(f"ix_inboundcommenttriggerrule_{column}"),
            table_name="inboundcommenttriggerrule",
        )
    op.drop_table("inboundcommenttriggerrule")
