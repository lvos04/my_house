"""Leg het doel van een koppeling vast en begrens koppelingen zonder vervaldatum."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("pairing_codes", "api_clients"):
        op.add_column(table, sa.Column("purpose", sa.String(16), nullable=False, server_default="phone"))
        op.create_check_constraint(
            f"ck_{table}_purpose", table, "purpose = 'phone' OR (purpose = 'hub' AND role = 'viewer')"
        )
    op.execute(
        "UPDATE api_clients SET expires_at = CASE "
        "WHEN isfinite(created_at) THEN LEAST(created_at, now()) + interval '4320 hours' "
        "ELSE now() END WHERE expires_at IS NULL"
    )


def downgrade() -> None:
    # Een versie zonder doel kan het Hub-recht niet handhaven. Trek het in vóór het doel verdwijnt.
    # Houd intussen nieuwe inwisselingen tegen, in dezelfde tabelvolgorde als de koppelroute.
    op.execute("LOCK TABLE pairing_codes, api_clients IN ACCESS EXCLUSIVE MODE")
    op.execute("UPDATE api_clients SET revoked_at = now() WHERE purpose = 'hub' AND revoked_at IS NULL")
    op.execute("UPDATE pairing_codes SET used_at = now() WHERE purpose = 'hub' AND used_at IS NULL")
    for table in ("api_clients", "pairing_codes"):
        op.drop_constraint(f"ck_{table}_purpose", table, type_="check")
        op.drop_column(table, "purpose")
