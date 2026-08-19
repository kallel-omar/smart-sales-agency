"""add ai employee tool access governance

Revision ID: 20260820_003
Revises: 20260820_002
Create Date: 2026-08-20 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260820_003"
down_revision: Union[str, None] = "20260820_002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "ai_employee_capability_tool_access" in inspector.get_table_names():
        return

    op.create_table(
        "ai_employee_capability_tool_access",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("integration_account_id", sa.Uuid(), nullable=False),
        sa.Column("action_type", sa.String(length=100), nullable=False),
        sa.Column("autonomy_level", sa.String(length=100), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["assignment_id"],
            ["ai_employee_capability_assignment.id"],
        ),
        sa.ForeignKeyConstraint(
            ["integration_account_id"],
            ["integrationaccount.id"],
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "assignment_id",
            "integration_account_id",
            "action_type",
            name="uq_ai_employee_capability_tool_access",
        ),
    )
    op.create_index(
        op.f("ix_ai_employee_capability_tool_access_action_type"),
        "ai_employee_capability_tool_access",
        ["action_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_employee_capability_tool_access_active"),
        "ai_employee_capability_tool_access",
        ["active"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_employee_capability_tool_access_assignment_id"),
        "ai_employee_capability_tool_access",
        ["assignment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_employee_capability_tool_access_autonomy_level"),
        "ai_employee_capability_tool_access",
        ["autonomy_level"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_employee_capability_tool_access_integration_account_id"),
        "ai_employee_capability_tool_access",
        ["integration_account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_employee_capability_tool_access_workspace_id"),
        "ai_employee_capability_tool_access",
        ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "ai_employee_capability_tool_access" not in inspector.get_table_names():
        return

    op.drop_index(
        op.f("ix_ai_employee_capability_tool_access_workspace_id"),
        table_name="ai_employee_capability_tool_access",
    )
    op.drop_index(
        op.f("ix_ai_employee_capability_tool_access_integration_account_id"),
        table_name="ai_employee_capability_tool_access",
    )
    op.drop_index(
        op.f("ix_ai_employee_capability_tool_access_autonomy_level"),
        table_name="ai_employee_capability_tool_access",
    )
    op.drop_index(
        op.f("ix_ai_employee_capability_tool_access_assignment_id"),
        table_name="ai_employee_capability_tool_access",
    )
    op.drop_index(
        op.f("ix_ai_employee_capability_tool_access_active"),
        table_name="ai_employee_capability_tool_access",
    )
    op.drop_index(
        op.f("ix_ai_employee_capability_tool_access_action_type"),
        table_name="ai_employee_capability_tool_access",
    )
    op.drop_table("ai_employee_capability_tool_access")
