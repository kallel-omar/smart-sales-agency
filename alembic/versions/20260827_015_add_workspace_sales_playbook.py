"""add workspace sales playbook foundation

Revision ID: 20260827_015
Revises: 20260826_014
Create Date: 2026-08-27 00:00:00

Downgrade drops the nullable column and therefore discards configured Playbooks.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260827_015"
down_revision: str | None = "20260826_014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("workspace")
    }
    if "sales_playbook" not in columns:
        op.add_column(
            "workspace",
            sa.Column("sales_playbook", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("workspace") as batch_op:
        batch_op.drop_column("sales_playbook")
