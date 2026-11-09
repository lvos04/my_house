"""follow-ups: pairing code role column, zone_log retention index

- pairing_codes.role (admin | control | viewer, default "control"). Migration 0002 kept the role of a
  code in the settings row "pairing_role:<pairing_codes.id>"; those rows are copied into the new
  column and removed.
- zone_log: an index on "at" for the hourly retention delete. The single-column index on zone_id
  from 0001 is dropped: the (zone_id, at) index from 0002 covers every lookup by zone.

Downgrade puts the role of codes that are still open back into settings rows (the 0002 workaround),
drops the column and the "at" index, and recreates the zone_id index.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pairing_codes",
        sa.Column("role", sa.String(length=16), nullable=False, server_default="control"),
    )
    op.execute(
        "UPDATE pairing_codes AS pc SET role = s.value->>'v' "
        "FROM settings AS s "
        "WHERE s.key = 'pairing_role:' || pc.id::text "
        "AND s.value->>'v' IN ('admin', 'control', 'viewer')"
    )
    op.execute("DELETE FROM settings WHERE key LIKE 'pairing\\_role:%'")
    op.create_check_constraint("ck_pairing_codes_role", "pairing_codes", "role IN ('admin', 'control', 'viewer')")

    op.create_index("ix_zone_log_at", "zone_log", ["at"], unique=False)
    op.execute("DROP INDEX IF EXISTS ix_zone_log_zone_id")


def downgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_zone_log_zone_id ON zone_log (zone_id)")
    op.drop_index("ix_zone_log_at", table_name="zone_log")

    op.execute(
        "INSERT INTO settings (key, value) "
        "SELECT 'pairing_role:' || id::text, jsonb_build_object('v', role) FROM pairing_codes "
        "WHERE used_at IS NULL AND expires_at > now() "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
    )
    op.drop_constraint("ck_pairing_codes_role", "pairing_codes", type_="check")
    op.drop_column("pairing_codes", "role")
