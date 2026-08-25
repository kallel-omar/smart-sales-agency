"""add integration provider authentication mode

Revision ID: 20260825_012
Revises: 20260820_011
Create Date: 2026-08-25 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260825_012"
down_revision: str | None = "20260820_011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("integrationaccount")}

    if "provider_auth_mode" not in columns:
        op.add_column(
            "integrationaccount",
            sa.Column("provider_auth_mode", sa.String(length=100), nullable=True),
        )

    op.execute(
        sa.text(
            "UPDATE integrationaccount "
            "SET provider_auth_mode = 'facebook_login' "
            "WHERE provider = 'instagram_dm' AND provider_auth_mode IS NULL"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("integrationaccount")}

    if "provider_auth_mode" in columns:
        op.drop_column("integrationaccount", "provider_auth_mode")
