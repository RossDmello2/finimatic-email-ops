"""Enable Supabase RLS on public application tables.

Revision ID: 0013_supabase_public_rls
Revises: 0012_phase17_queue_schedule_source
Create Date: 2026-06-08
"""

from alembic import op
import sqlalchemy as sa


revision = "0013_supabase_public_rls"
down_revision = "0012_phase17_queue_schedule_source"
branch_labels = None
depends_on = None


def _public_table_names(connection) -> list[str]:
    inspector = sa.inspect(connection)
    return sorted(inspector.get_table_names(schema="public" if connection.dialect.name == "postgresql" else None))


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    preparer = connection.dialect.identifier_preparer
    for table_name in _public_table_names(connection):
        quoted = preparer.quote(table_name)
        connection.execute(sa.text(f"ALTER TABLE public.{quoted} ENABLE ROW LEVEL SECURITY"))


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    preparer = connection.dialect.identifier_preparer
    for table_name in _public_table_names(connection):
        quoted = preparer.quote(table_name)
        connection.execute(sa.text(f"ALTER TABLE public.{quoted} DISABLE ROW LEVEL SECURITY"))
