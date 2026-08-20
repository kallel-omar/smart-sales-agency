"""add AI execution attribution

Revision ID: 20260820_006
Revises: 20260820_005
Create Date: 2026-08-20 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260820_006"
down_revision: str | None = "20260820_005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "aiinvocationusage"
ATTRIBUTION_COLUMNS = (
    ("department_id", "department.id"),
    ("ai_employee_id", "ai_employee.id"),
    ("capability_id", "capability.id"),
    ("work_item_id", "work_item.id"),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns(TABLE_NAME)}
    for column_name, foreign_key in ATTRIBUTION_COLUMNS:
        if column_name not in columns:
            op.add_column(
                TABLE_NAME,
                sa.Column(
                    column_name,
                    sa.Uuid(),
                    sa.ForeignKey(foreign_key),
                    nullable=True,
                ),
            )

    inspector = sa.inspect(bind)
    index_names = {
        index["name"] for index in inspector.get_indexes(TABLE_NAME)
    }
    for column_name, _ in ATTRIBUTION_COLUMNS:
        index_name = op.f(f"ix_{TABLE_NAME}_{column_name}")
        if index_name not in index_names:
            op.create_index(
                index_name,
                TABLE_NAME,
                [column_name],
                unique=False,
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns(TABLE_NAME)}
    index_names = {
        index["name"] for index in inspector.get_indexes(TABLE_NAME)
    }
    for column_name, _ in reversed(ATTRIBUTION_COLUMNS):
        if column_name not in columns:
            continue
        index_name = op.f(f"ix_{TABLE_NAME}_{column_name}")
        if index_name in index_names:
            op.drop_index(index_name, table_name=TABLE_NAME)
        op.drop_column(TABLE_NAME, column_name)
