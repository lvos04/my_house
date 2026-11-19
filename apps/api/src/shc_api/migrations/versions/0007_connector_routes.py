"""Bewaar connectorvoorstellen en bewijs en versieer de doelresources."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from shc_api.connector_proof import OPEN_RECOVERY

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("devices", "climate_zones"):
        op.add_column(table, sa.Column("connector_version", sa.BigInteger, nullable=False, server_default="1"))

    # Vergelijk alleen velden die doel, configuratie of bedienbare stand wijzigen.
    # Polltijd, sensortemperatuur en interne regelaaradministratie mogen een ETag niet laten verlopen.
    op.execute("""
        CREATE FUNCTION bump_connector_version() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_TABLE_NAME = 'devices' THEN
                IF ROW(NEW.home_id, NEW.integration_id, NEW.stable_identity, NEW.kind, NEW.provider,
                       NEW.lifecycle, NEW.compatibility, NEW.capabilities, NEW.state->'caps',
                       NEW.state->'member_identities', NEW.state->'members_complete', NEW.state->'group_device_id',
                       NEW.state->'on', NEW.state->'brightness')
                   IS DISTINCT FROM
                   ROW(OLD.home_id, OLD.integration_id, OLD.stable_identity, OLD.kind, OLD.provider,
                       OLD.lifecycle, OLD.compatibility, OLD.capabilities, OLD.state->'caps',
                       OLD.state->'member_identities', OLD.state->'members_complete', OLD.state->'group_device_id',
                       OLD.state->'on', OLD.state->'brightness') THEN
                    NEW.connector_version := OLD.connector_version + 1;
                END IF;
            ELSIF ROW(NEW.home_id, NEW.unit_device_id, NEW.sensor_device_id, NEW.outdoor_group, NEW.profile_id,
                      NEW.policy, NEW.control, NEW.override_until, NEW.desired_mode, NEW.desired_target_c)
                IS DISTINCT FROM
                  ROW(OLD.home_id, OLD.unit_device_id, OLD.sensor_device_id, OLD.outdoor_group, OLD.profile_id,
                      OLD.policy, OLD.control, OLD.override_until, OLD.desired_mode, OLD.desired_target_c) THEN
                NEW.connector_version := OLD.connector_version + 1;
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    for table in ("devices", "climate_zones"):
        op.execute(f"CREATE TRIGGER connector_version BEFORE UPDATE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION bump_connector_version()")

    op.create_table(
        "connector_changes",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("client", sa.Text, nullable=False),
        sa.Column("proposal_key", sa.Uuid, nullable=False),
        sa.Column("action_id", sa.String(32), nullable=False),
        sa.Column("target", JSONB, nullable=False),
        sa.Column("parameters", JSONB, nullable=False),
        sa.Column("actor_hash", sa.String(71), nullable=False),
        sa.Column("resource_etag", sa.String(71), nullable=False),
        sa.Column("preview_hash", sa.String(71), nullable=False),
        sa.Column("before", JSONB),
        sa.Column("after", JSONB),
        sa.Column("rollback_from", JSONB),
        sa.Column("rollback_after", JSONB),
        sa.Column("observed_at", sa.DateTime(timezone=True)),
        sa.Column("after_at", sa.DateTime(timezone=True)),
        sa.Column("rollback_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("execution_expires_at", sa.DateTime(timezone=True)),
        sa.Column("rollback_expires_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("rollback_status", sa.String(24)),
        sa.Column("reason_code", sa.String(64)),
        sa.Column("rollback_reason_code", sa.String(64)),
        sa.Column("command_id", sa.Uuid, unique=True),
        sa.Column("rollback_command_id", sa.Uuid, unique=True),
        sa.UniqueConstraint("client", "proposal_key"),
    )
    # Ook de oude logische export bewaart settings. Zet alleen onze eigen geschiedenis terug.
    op.execute("""
        WITH history AS (
            DELETE FROM settings
            WHERE value->>'format' = 'connector-0007' AND key = 'connector_history:' || (value->'v'->>'id')
            RETURNING value->'v' AS proof
        )
        INSERT INTO connector_changes SELECT (jsonb_populate_record(NULL::connector_changes, proof)).* FROM history
    """)


def downgrade() -> None:
    # Hou nieuwe opdrachten en een worker tussen claim en verzending buiten de terugval.
    op.execute("SELECT pg_advisory_xact_lock(hashtextextended('shc-commissioning', 0))")
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM commands cmd WHERE cmd.source = 'connector'
                  AND (cmd.status IN ('sending', 'sent')
                       OR cmd.status IN ('confirmed', 'failed') AND NOT EXISTS (
                           SELECT 1 FROM connector_changes c
                           WHERE cmd.id IN (c.command_id, c.rollback_command_id)
                              OR c.rollback_from->'previous_attempts' ? CAST(cmd.id AS text)
                       ))
            ) OR EXISTS (
                SELECT 1 FROM connector_changes c WHERE {OPEN_RECOVERY}
            ) THEN
                RAISE EXCEPTION 'Apparaatherstel staat open of onzeker; schema 0007 blijft behouden.';
            END IF;
        END;
        $$
    """)  # noqa: S608 - OPEN_RECOVERY is vaste broncode
    # Een oudere worker kent de voorwaarden voor nog niet verzonden connectorcommando's niet.
    op.execute("""
        UPDATE commands SET status = 'cancelled', detail = 'Connectorbediening vervallen bij schemaherstel'
        WHERE source = 'connector' AND status IN ('queued', 'sending', 'sent')
    """)
    op.execute("""
        INSERT INTO settings (key, value)
        SELECT 'connector_history:' || c.id, jsonb_build_object('format', 'connector-0007', 'v', to_jsonb(c))
        FROM connector_changes c
    """)
    op.execute("""
        DELETE FROM idempotency_keys
        WHERE scope IN ('connector:voorstel', 'connector:voorstel:2') AND key IN (SELECT id FROM connector_changes)
    """)
    op.drop_table("connector_changes")
    for table in ("climate_zones", "devices"):
        op.execute(f"DROP TRIGGER connector_version ON {table}")
        op.drop_column(table, "connector_version")
    op.execute("DROP FUNCTION bump_connector_version()")
