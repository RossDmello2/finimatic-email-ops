"""Add browser OIDC login transactions and opaque operator sessions.

Revision ID: 0011_phase17_operator_sessions
Revises: 0010_phase17_schema_convergence
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_phase17_operator_sessions"
down_revision = "0010_phase17_schema_convergence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_login_transactions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("flow_token_hash", sa.String(), nullable=False),
        sa.Column("state_hash", sa.String(), nullable=False),
        sa.Column("nonce", sa.String(), nullable=False),
        sa.Column("code_verifier_encrypted", sa.Text(), nullable=False),
        sa.Column("return_path", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_auth_login_transactions_flow_token_hash",
        "auth_login_transactions",
        ["flow_token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_auth_login_transactions_state_hash",
        "auth_login_transactions",
        ["state_hash"],
        unique=True,
    )

    op.create_table(
        "operator_sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_token_hash", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("roles_json", sa.Text(), nullable=False),
        sa.Column("issuer", sa.String(), nullable=False),
        sa.Column("audience", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_operator_sessions_session_token_hash",
        "operator_sessions",
        ["session_token_hash"],
        unique=True,
    )
    op.create_index("ix_operator_sessions_subject", "operator_sessions", ["subject"])
    op.create_index("ix_operator_sessions_expires_at", "operator_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_operator_sessions_expires_at", table_name="operator_sessions")
    op.drop_index("ix_operator_sessions_subject", table_name="operator_sessions")
    op.drop_index("ix_operator_sessions_session_token_hash", table_name="operator_sessions")
    op.drop_table("operator_sessions")
    op.drop_index("ix_auth_login_transactions_state_hash", table_name="auth_login_transactions")
    op.drop_index("ix_auth_login_transactions_flow_token_hash", table_name="auth_login_transactions")
    op.drop_table("auth_login_transactions")
