"""add workspace ai employees

Revision ID: 20260820_001
Revises: 20260819_002
Create Date: 2026-08-20 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260820_001"
down_revision: Union[str, None] = "20260819_002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "ai_employee" in inspector.get_table_names():
        return

    op.create_table(
        "ai_employee",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("role_key", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["department_id"], ["department.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_employee_active"), "ai_employee", ["active"], unique=False)
    op.create_index(
        op.f("ix_ai_employee_department_id"),
        "ai_employee",
        ["department_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_employee_role_key"),
        "ai_employee",
        ["role_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_employee_workspace_id"),
        "ai_employee",
        ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "ai_employee" not in inspector.get_table_names():
        return

    op.drop_index(op.f("ix_ai_employee_workspace_id"), table_name="ai_employee")
    op.drop_index(op.f("ix_ai_employee_role_key"), table_name="ai_employee")
    op.drop_index(op.f("ix_ai_employee_department_id"), table_name="ai_employee")
    op.drop_index(op.f("ix_ai_employee_active"), table_name="ai_employee")
    op.drop_table("ai_employee")
