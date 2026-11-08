"""improvements foundation

Schema groundwork for the verbeterlijst: login lockout and phone roles/expiry, stable
integration identity and diagnostics, sensor calibration, comfort profiles, command
tracking on climate zones, experiment automation, wider Hue button ids plus event
dedupe, T1/T2 tariffs with one tariff per day, hourly energy totals, backup metadata
and indexes for retention.

NOT NULL columns added to existing tables get a server default so existing rows are
valid. Data steps: integrations.identity is filled from the integration's single linked
device when unambiguous; duplicate tariffs per (home, valid_from) are reduced to the
most recently written row before the unique constraint is added.

Downgrade drops everything again. It cannot bring back deleted duplicate tariffs, and it
deletes button mappings whose button/event ids no longer fit the old 32 characters.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
EMPTY_OBJECT = sa.text("'{}'::jsonb")
EMPTY_ARRAY = sa.text("'[]'::jsonb")


def jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    # --- users: login lockout, password age -------------------------------------------
    op.add_column("users", sa.Column("failed_logins", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.add_column("users", sa.Column("locked_until", TS, nullable=True))
    op.add_column("users", sa.Column("password_changed_at", TS, nullable=True))

    # --- api_clients: role per phone (existing phones keep full admin rights), expiry ---
    op.add_column("api_clients", sa.Column("role", sa.String(length=16), server_default="admin", nullable=False))
    op.add_column("api_clients", sa.Column("expires_at", TS, nullable=True))
    op.add_column("api_clients", sa.Column("refreshed_at", TS, nullable=True))

    # --- integrations: stable identity of the paired device, diagnostics -------------
    op.add_column("integrations", sa.Column("identity", sa.String(length=128), nullable=True))
    op.add_column("integrations", sa.Column("diagnostics", jsonb(), server_default=EMPTY_OBJECT, nullable=False))
    # Only when unambiguous: the integration has exactly one device, and no other integration of the
    # same provider in the same home would get the same identity.
    op.execute(
        """
        WITH single AS (
            SELECT integration_id, min(stable_identity) AS identity
            FROM devices
            WHERE integration_id IS NOT NULL
            GROUP BY integration_id
            HAVING count(*) = 1
        ),
        candidate AS (
            SELECT i.id, s.identity, count(*) OVER (PARTITION BY i.home_id, i.provider, s.identity) AS n
            FROM integrations i
            JOIN single s ON s.integration_id = i.id
        )
        UPDATE integrations
        SET identity = candidate.identity
        FROM candidate
        WHERE integrations.id = candidate.id AND candidate.n = 1
        """
    )
    op.create_index(
        "uq_integrations_identity",
        "integrations",
        ["home_id", "provider", "identity"],
        unique=True,
        postgresql_where=sa.text("identity IS NOT NULL"),
    )

    # --- devices: calibration offsets, diagnostics, manual "tested" mark ---------------
    op.add_column("devices", sa.Column("calibration", jsonb(), server_default=EMPTY_OBJECT, nullable=False))
    op.add_column("devices", sa.Column("diagnostics", jsonb(), server_default=EMPTY_OBJECT, nullable=False))
    op.add_column("devices", sa.Column("tested_at", TS, nullable=True))

    # --- comfort_profiles (new) ----------------------------------------------------------
    op.create_table(
        "comfort_profiles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("home_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("target_c", sa.Float(), nullable=False),
        sa.Column("band_c", sa.Float(), server_default=sa.text("0.5"), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=True),
        sa.Column("energy_priority", sa.String(length=12), server_default="balanced", nullable=False),
        sa.Column("schedule", jsonb(), server_default=EMPTY_ARRAY, nullable=False),
        sa.Column("created_at", TS, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["home_id"], ["homes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("home_id", "name"),
    )

    # --- climate_zones: command tracking, correction, profile, watchdog -----------------
    op.add_column("climate_zones", sa.Column("pending_command_id", sa.UUID(), nullable=True))
    op.add_column("climate_zones", sa.Column("pending_since", TS, nullable=True))
    op.add_column(
        "climate_zones", sa.Column("mismatch_count", sa.Integer(), server_default=sa.text("0"), nullable=False)
    )
    op.add_column("climate_zones", sa.Column("correction_c", sa.Float(), server_default=sa.text("0"), nullable=False))
    op.add_column("climate_zones", sa.Column("profile_id", sa.UUID(), nullable=True))
    op.add_column("climate_zones", sa.Column("last_decision", sa.String(length=32), nullable=True))
    op.add_column("climate_zones", sa.Column("watchdog_since", TS, nullable=True))
    op.create_foreign_key(
        "climate_zones_profile_id_fkey",
        "climate_zones",
        "comfort_profiles",
        ["profile_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # --- experiments: zone, running variant, automatic apply/pause ---------------------
    op.add_column("experiments", sa.Column("zone_id", sa.UUID(), nullable=True))
    op.add_column("experiments", sa.Column("current_variant", sa.Integer(), nullable=True))
    op.add_column("experiments", sa.Column("auto_apply", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("experiments", sa.Column("paused_reason", sa.Text(), nullable=True))
    op.create_foreign_key(
        "experiments_zone_id_fkey",
        "experiments",
        "climate_zones",
        ["zone_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # --- button_mappings: Hue v2 ids are 36 chars; per-mapping on/off ------------------
    op.alter_column(
        "button_mappings",
        "button",
        type_=sa.String(length=64),
        existing_type=sa.String(length=32),
        existing_nullable=False,
    )
    op.alter_column(
        "button_mappings",
        "event",
        type_=sa.String(length=64),
        existing_type=sa.String(length=32),
        existing_nullable=False,
    )
    op.add_column("button_mappings", sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False))

    # --- processed_events (new): dedupe ledger for push events --------------------------
    op.create_table(
        "processed_events",
        sa.Column("event_key", sa.String(length=200), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=True),
        sa.Column("at", TS, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_key"),
    )
    op.create_index(op.f("ix_processed_events_at"), "processed_events", ["at"], unique=False)

    # --- tariffs: T1/T2 prices, DB defaults, one tariff per home and start date -------
    op.add_column("tariffs", sa.Column("import_t1_per_kwh", sa.Numeric(precision=8, scale=5), nullable=True))
    op.add_column("tariffs", sa.Column("import_t2_per_kwh", sa.Numeric(precision=8, scale=5), nullable=True))
    op.alter_column(
        "tariffs",
        "export_per_kwh",
        server_default=sa.text("0"),
        existing_type=sa.Numeric(precision=8, scale=5),
        existing_nullable=False,
    )
    op.alter_column(
        "tariffs",
        "fixed_per_day",
        server_default=sa.text("0"),
        existing_type=sa.Numeric(precision=8, scale=5),
        existing_nullable=False,
    )
    # Keep the most recently written row per (home, valid_from); ctid order approximates insertion order.
    op.execute(
        """
        DELETE FROM tariffs a
        USING tariffs b
        WHERE a.home_id = b.home_id AND a.valid_from = b.valid_from AND a.ctid < b.ctid
        """
    )
    op.create_unique_constraint("uq_tariffs_home_valid_from", "tariffs", ["home_id", "valid_from"])

    # --- energy_hourly (new): hourly totals per device and metric ----------------------
    op.create_table(
        "energy_hourly",
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("metric", sa.String(length=48), nullable=False),
        sa.Column("hour_start", TS, nullable=False),
        sa.Column("kwh", sa.Float(), nullable=False),
        sa.Column("first_value", sa.Float(), nullable=True),
        sa.Column("last_value", sa.Float(), nullable=True),
        sa.Column("samples", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("flags", jsonb(), server_default=EMPTY_ARRAY, nullable=False),
        sa.Column("updated_at", TS, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("device_id", "metric", "hour_start"),
    )
    op.create_index(op.f("ix_energy_hourly_hour_start"), "energy_hourly", ["hour_start"], unique=False)

    # --- backups: automatic vs manual, versions it was made with ----------------------
    op.add_column("backups", sa.Column("auto", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("backups", sa.Column("app_version", sa.String(length=32), nullable=True))
    op.add_column("backups", sa.Column("schema_revision", sa.String(length=64), nullable=True))

    # --- indexes for zone log paging and retention deletes ----------------------------
    op.create_index("ix_zone_log_zone_at", "zone_log", ["zone_id", "at"], unique=False)
    op.create_index("ix_readings_observed_at", "sensor_readings", ["observed_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_readings_observed_at", table_name="sensor_readings")
    op.drop_index("ix_zone_log_zone_at", table_name="zone_log")

    op.drop_column("backups", "schema_revision")
    op.drop_column("backups", "app_version")
    op.drop_column("backups", "auto")

    op.drop_index(op.f("ix_energy_hourly_hour_start"), table_name="energy_hourly")
    op.drop_table("energy_hourly")

    op.drop_constraint("uq_tariffs_home_valid_from", "tariffs", type_="unique")
    op.alter_column(
        "tariffs",
        "fixed_per_day",
        server_default=None,
        existing_type=sa.Numeric(precision=8, scale=5),
        existing_nullable=False,
    )
    op.alter_column(
        "tariffs",
        "export_per_kwh",
        server_default=None,
        existing_type=sa.Numeric(precision=8, scale=5),
        existing_nullable=False,
    )
    op.drop_column("tariffs", "import_t2_per_kwh")
    op.drop_column("tariffs", "import_t1_per_kwh")

    op.drop_index(op.f("ix_processed_events_at"), table_name="processed_events")
    op.drop_table("processed_events")

    op.drop_column("button_mappings", "enabled")
    # Mappings with ids longer than the old limit cannot be represented in the old schema.
    op.execute("DELETE FROM button_mappings WHERE length(button) > 32 OR length(event) > 32")
    op.alter_column(
        "button_mappings",
        "event",
        type_=sa.String(length=32),
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )
    op.alter_column(
        "button_mappings",
        "button",
        type_=sa.String(length=32),
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )

    op.drop_constraint("experiments_zone_id_fkey", "experiments", type_="foreignkey")
    op.drop_column("experiments", "paused_reason")
    op.drop_column("experiments", "auto_apply")
    op.drop_column("experiments", "current_variant")
    op.drop_column("experiments", "zone_id")

    op.drop_constraint("climate_zones_profile_id_fkey", "climate_zones", type_="foreignkey")
    op.drop_column("climate_zones", "watchdog_since")
    op.drop_column("climate_zones", "last_decision")
    op.drop_column("climate_zones", "profile_id")
    op.drop_column("climate_zones", "correction_c")
    op.drop_column("climate_zones", "mismatch_count")
    op.drop_column("climate_zones", "pending_since")
    op.drop_column("climate_zones", "pending_command_id")

    op.drop_table("comfort_profiles")

    op.drop_column("devices", "tested_at")
    op.drop_column("devices", "diagnostics")
    op.drop_column("devices", "calibration")

    op.drop_index("uq_integrations_identity", table_name="integrations")
    op.drop_column("integrations", "diagnostics")
    op.drop_column("integrations", "identity")

    op.drop_column("api_clients", "refreshed_at")
    op.drop_column("api_clients", "expires_at")
    op.drop_column("api_clients", "role")

    op.drop_column("users", "password_changed_at")
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_logins")
