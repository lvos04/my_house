"""Annuleer ongeclaimde connectorbediening op de grens tussen twee installaties."""

from hashlib import sha256
from pathlib import Path

import shc_integrations
from sqlalchemy import text

from .commissioning import LOCK_SQL
from .config import VERSION
from .connector_proof import CANCELLED_UNDO, INCOMPLETE_PROOF, OPEN_RECOVERY, PROPOSAL_SCOPE, RESTORE_HISTORY


def _software_scope() -> str:
    # Ontwikkel- en herstelbuilds kunnen hetzelfde VERSION-bestand hebben.
    digest = sha256(VERSION.encode())
    for root in (Path(__file__).parent, Path(shc_integrations.__file__).parent):
        for path in sorted(root.rglob("*.py")):
            digest.update(str(path.relative_to(root)).encode() + b"\0" + sha256(path.read_bytes()).digest())
    return "connector:voorstel:" + digest.hexdigest()


LOCAL_PROPOSAL = _software_scope()

# De bewijsrij zelf blijft bytegetrouw bewaard, ook voor een oudere lezer.
CANCEL_PENDING = f"""
    WITH cancelled AS (
        UPDATE commands SET status='cancelled', detail='versiewissel', updated_at=now()
        WHERE source='connector' AND status='queued' AND attempts=0
        RETURNING id, home_id
    ), revoked AS (
        DELETE FROM idempotency_keys k USING connector_changes c, cancelled cmd
        WHERE k.key=c.id AND {PROPOSAL_SCOPE} AND cmd.id IN (c.command_id, c.rollback_command_id)
    )
    INSERT INTO alerts (id, home_id, key, severity, title, detail, source, created_at)
    SELECT gen_random_uuid(), cmd.home_id, 'connector:rollback:' || cmd.id, 'warning',
           'Handmatige controle nodig', 'Rollback ' || cmd.id || ' is geannuleerd wegens versiewissel.',
           'connector', now()
    FROM cancelled cmd JOIN connector_changes c ON c.rollback_command_id=cmd.id
"""  # noqa: S608 - vaste voorstelrechten


# Bij een SQL-terugval wordt dit binnen de importtransactie uitgevoerd, ook door de oude software.
REVOKE_RIGHTS = f"DELETE FROM idempotency_keys k WHERE {PROPOSAL_SCOPE}"  # noqa: S608 - constante scopes
OPEN_AFTER_SWITCH = f"""
    SELECT EXISTS (SELECT FROM commands WHERE source='connector' AND status IN ('queued','sending','sent'))
        OR EXISTS (SELECT FROM connector_changes c WHERE ({OPEN_RECOVERY}) AND ({CANCELLED_UNDO}) IS NOT TRUE)
"""  # noqa: S608 - vaste bewijsvoorwaarden
FALLBACK_SQL = f"""
-- my_house: connectoropdrachten vervallen bij versiewissel
DO $overgang$ BEGIN
    -- pg_dump zet het zoekpad tijdens een import leeg.
    PERFORM pg_catalog.set_config('search_path', 'pg_catalog, public', true);
    IF to_regclass('public.connector_changes') IS NULL THEN
        IF to_regclass('public.commands') IS NOT NULL THEN
            IF EXISTS (SELECT FROM commands WHERE source='connector' AND status IN ('queued','sending','sent')) THEN
                RAISE EXCEPTION 'Het connectorherstelbewijs van de vorige installatie ontbreekt.';
            END IF;
        END IF;
        RETURN;
    END IF;
    {RESTORE_HISTORY};
    {CANCEL_PENDING};
    IF ({INCOMPLETE_PROOF}) OR ({OPEN_AFTER_SWITCH}) THEN
        RAISE EXCEPTION 'De vorige installatie bevat open herstel of onvolledig connectorherstelbewijs.';
    END IF;
    {REVOKE_RIGHTS};
END $overgang$;
""".encode()


def foreign_work(conn) -> bool:
    return conn.scalar(text(f"""
        SELECT EXISTS (SELECT FROM idempotency_keys k WHERE {PROPOSAL_SCOPE} AND k.scope <> :scope)
            OR EXISTS (SELECT FROM commands cmd WHERE cmd.source='connector' AND cmd.status='queued'
                       AND NOT EXISTS (SELECT FROM connector_changes c JOIN idempotency_keys k ON k.key=c.id
                                       WHERE cmd.id IN (c.command_id,c.rollback_command_id) AND k.scope=:scope))
    """), {"scope": LOCAL_PROPOSAL})  # noqa: S608 - vaste voorstelrechten


def cancel_for_version(conn, *, all_rights: bool = True) -> None:
    """Onder dezelfde lock als de claim. Een fout draait historie, intrekking en melding samen terug."""
    from .cli import MigrationError, _check_connector_rows

    conn.execute(text(LOCK_SQL))
    conn.execute(text(RESTORE_HISTORY))
    conn.execute(text(CANCEL_PENDING))
    _check_connector_rows(conn)
    if not all_rights:
        return
    if conn.scalar(text(OPEN_AFTER_SWITCH)):
        raise MigrationError(
            "Apparaatherstel staat open of onzeker. Controleer de teruglezing van de vorige installatie."
        )
    conn.execute(text(REVOKE_RIGHTS))
