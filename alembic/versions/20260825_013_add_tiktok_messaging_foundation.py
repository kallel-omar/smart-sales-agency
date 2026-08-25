"""add TikTok messaging account foundation

Revision ID: 20260825_013
Revises: 20260825_012
Create Date: 2026-08-25 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260825_013"
down_revision: str | None = "20260825_012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "uq_active_tiktok_dm_external_account"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("integrationaccount")}
    if "comment_to_message_eligible" not in columns:
        op.add_column(
            "integrationaccount",
            sa.Column(
                "comment_to_message_eligible",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
        op.create_index(
            op.f("ix_integrationaccount_comment_to_message_eligible"),
            "integrationaccount",
            ["comment_to_message_eligible"],
            unique=False,
        )

    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("integrationaccount")}
    if INDEX_NAME not in indexes:
        predicate = (
            sa.text(
                "provider = 'tiktok_dm' AND external_account_id IS NOT NULL "
                "AND active IS TRUE"
            )
            if bind.dialect.name == "postgresql"
            else sa.text(
                "provider = 'tiktok_dm' AND external_account_id IS NOT NULL "
                "AND active = 1"
            )
        )
        dialect_options = (
            {"postgresql_where": predicate}
            if bind.dialect.name == "postgresql"
            else {"sqlite_where": predicate}
        )
        op.create_index(
            INDEX_NAME,
            "integrationaccount",
            ["external_account_id"],
            unique=True,
            **dialect_options,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("integrationaccount")}
    if INDEX_NAME in indexes:
        op.drop_index(INDEX_NAME, table_name="integrationaccount")

    columns = {column["name"] for column in sa.inspect(bind).get_columns("integrationaccount")}
    if "comment_to_message_eligible" in columns:
        eligibility_index = op.f("ix_integrationaccount_comment_to_message_eligible")
        indexes = {
            index["name"] for index in sa.inspect(bind).get_indexes("integrationaccount")
        }
        if eligibility_index in indexes:
            op.drop_index(eligibility_index, table_name="integrationaccount")
        op.drop_column("integrationaccount", "comment_to_message_eligible")
