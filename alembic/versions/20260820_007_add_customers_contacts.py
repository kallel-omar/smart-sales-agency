"""add shared customers and contacts

Revision ID: 20260820_007
Revises: 20260820_006
Create Date: 2026-08-20 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260820_007"
down_revision: str | None = "20260820_006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    if "customer" not in tables:
        op.create_table(
            "customer",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("workspace_id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_customer_workspace_id"), "customer", ["workspace_id"])
        op.create_index(op.f("ix_customer_name"), "customer", ["name"])
    if "contact" not in tables:
        op.create_table(
            "contact",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("workspace_id", sa.Uuid(), nullable=False),
            sa.Column("customer_id", sa.Uuid(), nullable=True),
            sa.Column("name", sa.String(length=200), nullable=True),
            sa.Column("email", sa.String(length=320), nullable=True),
            sa.Column("phone", sa.String(length=50), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
            sa.ForeignKeyConstraint(["customer_id"], ["customer.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_contact_workspace_id"), "contact", ["workspace_id"])
        op.create_index(op.f("ix_contact_customer_id"), "contact", ["customer_id"])
        op.create_index(op.f("ix_contact_email"), "contact", ["email"])

    inspector = sa.inspect(bind)
    if "lead" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("lead")}
    if "contact_id" not in columns:
        op.add_column(
            "lead",
            sa.Column("contact_id", sa.Uuid(), sa.ForeignKey("contact.id"), nullable=True),
        )
    index_names = {index["name"] for index in sa.inspect(bind).get_indexes("lead")}
    index_name = op.f("ix_lead_contact_id")
    if index_name not in index_names:
        op.create_index(index_name, "lead", ["contact_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "lead" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("lead")}
        if "contact_id" in columns:
            index_name = op.f("ix_lead_contact_id")
            index_names = {index["name"] for index in inspector.get_indexes("lead")}
            if index_name in index_names:
                op.drop_index(index_name, table_name="lead")
            op.drop_column("lead", "contact_id")
    inspector = sa.inspect(bind)
    if "contact" in inspector.get_table_names():
        op.drop_index(op.f("ix_contact_email"), table_name="contact")
        op.drop_index(op.f("ix_contact_customer_id"), table_name="contact")
        op.drop_index(op.f("ix_contact_workspace_id"), table_name="contact")
        op.drop_table("contact")
    if "customer" in inspector.get_table_names():
        op.drop_index(op.f("ix_customer_name"), table_name="customer")
        op.drop_index(op.f("ix_customer_workspace_id"), table_name="customer")
        op.drop_table("customer")
