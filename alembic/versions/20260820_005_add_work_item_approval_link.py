"""add work item approval link

Revision ID: 20260820_005
Revises: 20260820_004
Create Date: 2026-08-20 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260820_005"
down_revision: Union[str, None] = "20260820_004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("approvalrequest")}
    if "work_item_id" not in columns:
        op.add_column(
            "approvalrequest",
            sa.Column(
                "work_item_id",
                sa.Uuid(),
                sa.ForeignKey("work_item.id"),
                nullable=True,
            ),
        )

    index_names = {
        index["name"] for index in inspector.get_indexes("approvalrequest")
    }
    index_name = op.f("ix_approvalrequest_work_item_id")
    if index_name not in index_names:
        op.create_index(
            index_name,
            "approvalrequest",
            ["work_item_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("approvalrequest")}
    if "work_item_id" not in columns:
        return

    index_names = {
        index["name"] for index in inspector.get_indexes("approvalrequest")
    }
    index_name = op.f("ix_approvalrequest_work_item_id")
    if index_name in index_names:
        op.drop_index(index_name, table_name="approvalrequest")
    op.drop_column("approvalrequest", "work_item_id")
