"""add workspace departments

Revision ID: 20260819_001
Revises: 20260811_297
Create Date: 2026-08-19 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260819_001"
down_revision: Union[str, None] = "20260811_297"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "department" in inspector.get_table_names():
        return

    op.create_table(
        "department",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "kind",
            name="uq_department_workspace_kind",
        ),
    )
    op.create_index(op.f("ix_department_kind"), "department", ["kind"], unique=False)
    op.create_index(
        op.f("ix_department_workspace_id"),
        "department",
        ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "department" not in inspector.get_table_names():
        return

    op.drop_index(op.f("ix_department_workspace_id"), table_name="department")
    op.drop_index(op.f("ix_department_kind"), table_name="department")
    op.drop_table("department")
