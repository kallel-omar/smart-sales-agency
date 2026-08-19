"""add ai employee capability assignments

Revision ID: 20260820_002
Revises: 20260820_001
Create Date: 2026-08-20 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260820_002"
down_revision: Union[str, None] = "20260820_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "ai_employee_capability_assignment" in inspector.get_table_names():
        return

    op.create_table(
        "ai_employee_capability_assignment",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("ai_employee_id", sa.Uuid(), nullable=False),
        sa.Column("capability_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["ai_employee_id"], ["ai_employee.id"]),
        sa.ForeignKeyConstraint(["capability_id"], ["capability.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "ai_employee_id",
            "capability_id",
            name="uq_ai_employee_capability_assignment",
        ),
    )
    op.create_index(
        op.f("ix_ai_employee_capability_assignment_ai_employee_id"),
        "ai_employee_capability_assignment",
        ["ai_employee_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_employee_capability_assignment_capability_id"),
        "ai_employee_capability_assignment",
        ["capability_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_employee_capability_assignment_created_at"),
        "ai_employee_capability_assignment",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_employee_capability_assignment_workspace_id"),
        "ai_employee_capability_assignment",
        ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "ai_employee_capability_assignment" not in inspector.get_table_names():
        return

    op.drop_index(
        op.f("ix_ai_employee_capability_assignment_workspace_id"),
        table_name="ai_employee_capability_assignment",
    )
    op.drop_index(
        op.f("ix_ai_employee_capability_assignment_created_at"),
        table_name="ai_employee_capability_assignment",
    )
    op.drop_index(
        op.f("ix_ai_employee_capability_assignment_capability_id"),
        table_name="ai_employee_capability_assignment",
    )
    op.drop_index(
        op.f("ix_ai_employee_capability_assignment_ai_employee_id"),
        table_name="ai_employee_capability_assignment",
    )
    op.drop_table("ai_employee_capability_assignment")
