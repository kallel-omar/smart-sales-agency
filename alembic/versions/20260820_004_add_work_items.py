"""add generic work items

Revision ID: 20260820_004
Revises: 20260820_003
Create Date: 2026-08-20 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260820_004"
down_revision: Union[str, None] = "20260820_003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "work_item" in inspector.get_table_names():
        return

    op.create_table(
        "work_item",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("ai_employee_id", sa.Uuid(), nullable=True),
        sa.Column("capability_id", sa.Uuid(), nullable=True),
        sa.Column("assignment_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("work_type", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["ai_employee_id"], ["ai_employee.id"]),
        sa.ForeignKeyConstraint(["assignment_id"], ["ai_employee_capability_assignment.id"]),
        sa.ForeignKeyConstraint(["capability_id"], ["capability.id"]),
        sa.ForeignKeyConstraint(["department_id"], ["department.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_work_item_ai_employee_id"), "work_item", ["ai_employee_id"])
    op.create_index(op.f("ix_work_item_assignment_id"), "work_item", ["assignment_id"])
    op.create_index(op.f("ix_work_item_capability_id"), "work_item", ["capability_id"])
    op.create_index(op.f("ix_work_item_completed_at"), "work_item", ["completed_at"])
    op.create_index(op.f("ix_work_item_correlation_id"), "work_item", ["correlation_id"])
    op.create_index(op.f("ix_work_item_created_at"), "work_item", ["created_at"])
    op.create_index(op.f("ix_work_item_department_id"), "work_item", ["department_id"])
    op.create_index(op.f("ix_work_item_error_code"), "work_item", ["error_code"])
    op.create_index(op.f("ix_work_item_expires_at"), "work_item", ["expires_at"])
    op.create_index(op.f("ix_work_item_started_at"), "work_item", ["started_at"])
    op.create_index(op.f("ix_work_item_status"), "work_item", ["status"])
    op.create_index(op.f("ix_work_item_work_type"), "work_item", ["work_type"])
    op.create_index(op.f("ix_work_item_workspace_id"), "work_item", ["workspace_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "work_item" not in inspector.get_table_names():
        return

    op.drop_index(op.f("ix_work_item_workspace_id"), table_name="work_item")
    op.drop_index(op.f("ix_work_item_work_type"), table_name="work_item")
    op.drop_index(op.f("ix_work_item_status"), table_name="work_item")
    op.drop_index(op.f("ix_work_item_started_at"), table_name="work_item")
    op.drop_index(op.f("ix_work_item_expires_at"), table_name="work_item")
    op.drop_index(op.f("ix_work_item_error_code"), table_name="work_item")
    op.drop_index(op.f("ix_work_item_department_id"), table_name="work_item")
    op.drop_index(op.f("ix_work_item_created_at"), table_name="work_item")
    op.drop_index(op.f("ix_work_item_correlation_id"), table_name="work_item")
    op.drop_index(op.f("ix_work_item_completed_at"), table_name="work_item")
    op.drop_index(op.f("ix_work_item_capability_id"), table_name="work_item")
    op.drop_index(op.f("ix_work_item_assignment_id"), table_name="work_item")
    op.drop_index(op.f("ix_work_item_ai_employee_id"), table_name="work_item")
    op.drop_table("work_item")
