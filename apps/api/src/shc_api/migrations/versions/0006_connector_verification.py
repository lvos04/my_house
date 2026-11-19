"""Voeg de Hub-pin, eenmalige verzoeknonces en idempotentieantwoorden toe."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connector_hub_pins",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("jwt_keys", postgresql.JSONB, nullable=False),
        sa.Column("policy_keys", postgresql.JSONB, nullable=False),
        sa.Column("fingerprint", sa.String(19), nullable=False),
        sa.Column("rotation_sequence", sa.BigInteger, nullable=False),
        sa.Column("accept_from", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1 AND rotation_sequence >= 0", name="ck_connector_hub_pins_single"),
    )
    op.create_table("connector_nonces", sa.Column("nonce", sa.Uuid, primary_key=True))
    op.create_table(
        "idempotency_keys",
        sa.Column("scope", sa.Text, primary_key=True),
        sa.Column("key", sa.Uuid, primary_key=True),
        sa.Column("request_hash", sa.LargeBinary, nullable=False),
        sa.Column("response_status", sa.Integer),
        sa.Column("response_body", postgresql.JSONB(none_as_null=True)),
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    op.drop_table("connector_nonces")
    op.drop_table("connector_hub_pins")
