"""add follow-up work item links

Revision ID: 20260820_010
Revises: 20260820_009
Create Date: 2026-08-20 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260820_010"
down_revision: str | None = "20260820_009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("work_item")}
    sqlite = bind.dialect.name == "sqlite"
    if "source_follow_up_task_id" not in columns:
        if sqlite:
            op.execute(
                "ALTER TABLE work_item ADD COLUMN source_follow_up_task_id "
                "CHAR(32) REFERENCES followuptask(id)"
            )
        else:
            op.add_column(
                "work_item",
                sa.Column("source_follow_up_task_id", sa.Uuid(), nullable=True),
            )
            op.create_foreign_key(
                "fk_work_item_source_follow_up_task_id",
                "work_item",
                "followuptask",
                ["source_follow_up_task_id"],
                ["id"],
            )
    if "parent_work_item_id" not in columns:
        if sqlite:
            op.execute(
                "ALTER TABLE work_item ADD COLUMN parent_work_item_id "
                "CHAR(32) REFERENCES work_item(id)"
            )
        else:
            op.add_column(
                "work_item",
                sa.Column("parent_work_item_id", sa.Uuid(), nullable=True),
            )
            op.create_foreign_key(
                "fk_work_item_parent_work_item_id",
                "work_item",
                "work_item",
                ["parent_work_item_id"],
                ["id"],
            )
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("work_item")}
    if "ix_work_item_source_follow_up_task_id" not in indexes:
        op.create_index(
            "ix_work_item_source_follow_up_task_id",
            "work_item",
            ["source_follow_up_task_id"],
            unique=True,
        )
    if "ix_work_item_parent_work_item_id" not in indexes:
        op.create_index(
            "ix_work_item_parent_work_item_id",
            "work_item",
            ["parent_work_item_id"],
            unique=True,
        )


def downgrade() -> None:
    op.drop_index("ix_work_item_parent_work_item_id", table_name="work_item")
    op.drop_index("ix_work_item_source_follow_up_task_id", table_name="work_item")
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint("fk_work_item_parent_work_item_id", "work_item", type_="foreignkey")
        op.drop_constraint("fk_work_item_source_follow_up_task_id", "work_item", type_="foreignkey")
    op.drop_column("work_item", "parent_work_item_id")
    op.drop_column("work_item", "source_follow_up_task_id")
