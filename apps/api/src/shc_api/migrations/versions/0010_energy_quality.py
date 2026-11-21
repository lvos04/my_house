"""Bewaar de puntkwaliteit van een uur en de bekendheid van de terugleververgoeding."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("energy_hourly", sa.Column("point_quality", sa.String(12), nullable=True))
    op.add_column("tariffs", sa.Column("export_price_known", sa.Boolean(), nullable=True))
    # Een oude worker vervangt bij herberekening alle flags. Zo'n uur heeft geen bronbewijs meer.
    op.execute("""
        UPDATE energy_hourly h SET point_quality = NULLIF(substring(q.flag FROM 15), ''),
            flags = (CASE WHEN h.flags ? 'quality_incomplete' THEN h.flags - 'incomplete' ELSE h.flags END)
                    - q.flag - 'quality_incomplete'
        FROM (
            SELECT device_id, metric, hour_start, flag
            FROM energy_hourly, jsonb_array_elements_text(flags) AS flag
            WHERE left(flag, 14) = 'point_quality:'
        ) q
        WHERE (h.device_id, h.metric, h.hour_start) = (q.device_id, q.metric, q.hour_start)
    """)
    op.execute("""
        UPDATE tariffs t SET export_price_known = (e.value->>'export_price_known')::boolean
        FROM settings s, jsonb_each(s.value->'v') e
        WHERE s.key = 'tariff_extras' AND e.key = t.id::text AND jsonb_typeof(e.value->'export_price_known') = 'boolean'
    """)
    op.execute("""
        UPDATE settings s SET value = jsonb_set(s.value, '{v}', COALESCE((
            SELECT jsonb_object_agg(e.key, e.value - 'export_price_known') FROM jsonb_each(s.value->'v') e
            WHERE e.value - 'export_price_known' <> '{}'::jsonb
        ), '{}'::jsonb)) WHERE s.key = 'tariff_extras' AND EXISTS (
            SELECT FROM jsonb_each(s.value->'v') e WHERE e.value ? 'export_price_known'
        )
    """)


def downgrade() -> None:
    # De oude export leest 'incomplete'. Ook daar kan een geschat of onbekend uur dus nooit gemeten worden.
    op.execute("""
        UPDATE energy_hourly SET flags = flags || jsonb_build_array('point_quality:' || COALESCE(point_quality, ''))
            || CASE WHEN point_quality IS DISTINCT FROM 'measured' AND NOT flags ? 'incomplete'
                    THEN '["incomplete", "quality_incomplete"]'::jsonb ELSE '[]'::jsonb END
    """)
    # De vorige tariefroute vervangt of verwijdert de extra's bij elke bewerking van dit tarief.
    # Daarmee vervalt ook de oude bevestiging van een nulprijs zodra iemand het tarief opnieuw invoert.
    op.execute("""
        LOCK TABLE tariffs, settings IN SHARE ROW EXCLUSIVE MODE
    """)
    op.execute("""
        INSERT INTO settings (key, value)
        SELECT 'tariff_extras', jsonb_build_object('v', jsonb_object_agg(t.id::text,
            COALESCE(s.value->'v'->(t.id::text), '{}'::jsonb)
                || jsonb_build_object('export_price_known', t.export_price_known)))
        FROM tariffs t LEFT JOIN settings s ON s.key = 'tariff_extras'
        WHERE t.export_price_known IS NOT NULL HAVING count(*) > 0
        ON CONFLICT (key) DO UPDATE SET value = jsonb_set(settings.value, '{v}',
            COALESCE(settings.value->'v', '{}'::jsonb) || (excluded.value->'v'))
    """)
    op.drop_column("tariffs", "export_price_known")
    op.drop_column("energy_hourly", "point_quality")
