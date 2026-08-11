"""baseline current schema

Revision ID: 20260811_297
Revises:
Create Date: 2026-08-11 00:00:00
"""

from typing import Sequence, Union

from alembic import op

from app.models import SQLModel

revision: str = "20260811_297"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    SQLModel.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    SQLModel.metadata.drop_all(bind=op.get_bind())
