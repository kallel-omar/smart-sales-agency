"""add workspace capabilities

Revision ID: 20260819_002
Revises: 20260819_001
Create Date: 2026-08-19 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260819_002"
down_revision: Union[str, None] = "20260819_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "capability" in inspector.get_table_names():
        return

    op.create_table(
        "capability",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["department_id"], ["department.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "department_id",
            "key",
            name="uq_capability_workspace_department_key",
        ),
    )
    op.create_index(op.f("ix_capability_active"), "capability", ["active"], unique=False)
    op.create_index(
        op.f("ix_capability_department_id"),
        "capability",
        ["department_id"],
        unique=False,
    )
    op.create_index(op.f("ix_capability_key"), "capability", ["key"], unique=False)
    op.create_index(
        op.f("ix_capability_workspace_id"),
        "capability",
        ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "capability" not in inspector.get_table_names():
        return

    op.drop_index(op.f("ix_capability_workspace_id"), table_name="capability")
    op.drop_index(op.f("ix_capability_key"), table_name="capability")
    op.drop_index(op.f("ix_capability_department_id"), table_name="capability")
    op.drop_index(op.f("ix_capability_active"), table_name="capability")
    op.drop_table("capability")
