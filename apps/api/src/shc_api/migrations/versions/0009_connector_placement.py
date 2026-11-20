"""Maak de plaatsing van een unit en de softwareversie bij een connectorclaim controleerbaar."""

from alembic import op

from shc_api.connector_transition import cancel_for_version

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cancel_for_version(op.get_bind())
    op.execute("""
        CREATE FUNCTION change_unit_placement() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF current_setting('shc.restoring_placements', true) = 'on' THEN RETURN NEW; END IF;
            IF TG_TABLE_NAME = 'devices' THEN
                NEW.connector_version := OLD.connector_version + 1;
                RETURN NEW;
            END IF;
            IF TG_OP <> 'UPDATE' OR ROW(OLD.unit_device_id, OLD.home_id, OLD.outdoor_group)
                IS DISTINCT FROM ROW(NEW.unit_device_id, NEW.home_id, NEW.outdoor_group) THEN
                UPDATE devices SET connector_version=connector_version+1
                WHERE kind='ac_unit' AND id IN (OLD.unit_device_id, NEW.unit_device_id);
            END IF;
            RETURN NULL;
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER unit_placement AFTER INSERT OR DELETE OR UPDATE OF unit_device_id, home_id, outdoor_group
        ON climate_zones FOR EACH ROW EXECUTE FUNCTION change_unit_placement()
    """)
    op.execute("""
        CREATE TRIGGER unit_room BEFORE UPDATE OF room_id ON devices FOR EACH ROW
        WHEN (NEW.kind='ac_unit' AND NEW.room_id IS DISTINCT FROM OLD.room_id)
        EXECUTE FUNCTION change_unit_placement()
    """)
    # Ook een vorige worker die tijdens de wissel al draaide, kan geen nieuwe opdracht claimen.
    op.execute("""
        CREATE FUNCTION check_connector_claim() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF current_setting('shc.connector_scope', true) IS NULL OR NOT EXISTS (
                SELECT FROM connector_changes c JOIN idempotency_keys k ON k.key=c.id
                WHERE NEW.id IN (c.command_id,c.rollback_command_id)
                  AND k.scope=current_setting('shc.connector_scope', true)
            ) THEN
                RAISE EXCEPTION 'De connectoropdracht hoort bij een andere softwareversie.';
            END IF;
            RETURN NEW;
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER connector_claim BEFORE UPDATE OF status ON commands FOR EACH ROW
        WHEN (OLD.source='connector' AND OLD.status='queued' AND NEW.status='sending')
        EXECUTE FUNCTION check_connector_claim()
    """)


def downgrade() -> None:
    cancel_for_version(op.get_bind())
    op.execute("DROP TRIGGER connector_claim ON commands")
    op.execute("DROP FUNCTION check_connector_claim()")
    op.execute("DROP TRIGGER unit_placement ON climate_zones")
    op.execute("DROP TRIGGER unit_room ON devices")
    op.execute("DROP FUNCTION change_unit_placement()")
