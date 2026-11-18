"""Bewaar detectiemethode, terugval en de eenmalige inbedrijfstellingstoestand.

Bestaande apparaatrijen, inclusief archief, betekenen een bestaande installatie.
Een terugweg verwijdert alleen de twee nieuwe kolommen; de gate-instelling blijft
bewaard zodat een eerder ingeschakelde beveiliging bij opnieuw opwaarderen aan blijft.
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("LOCK TABLE devices IN SHARE ROW EXCLUSIVE MODE")
    op.add_column("devices", sa.Column("detection_method", sa.String(16), nullable=True))
    op.add_column("devices", sa.Column("fallback", sa.Text(), nullable=True))
    op.create_check_constraint("ck_devices_detection_method", "devices", "detection_method IN ('scan', 'manual')")
    op.execute("""
        INSERT INTO settings (key, value)
        SELECT 'commissioning_gate', jsonb_build_object('v', jsonb_build_object(
            'version', 1,
            'enabled', NOT EXISTS (SELECT 1 FROM devices),
            'origin', CASE WHEN EXISTS (SELECT 1 FROM devices) THEN 'existing_devices' ELSE 'fresh' END,
            'enabled_at', CASE WHEN NOT EXISTS (SELECT 1 FROM devices)
                THEN to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
            'enabled_by', CASE WHEN NOT EXISTS (SELECT 1 FROM devices) THEN 'systeem' END
        ))
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_constraint("ck_devices_detection_method", "devices", type_="check")
    op.drop_column("devices", "fallback")
    op.drop_column("devices", "detection_method")
