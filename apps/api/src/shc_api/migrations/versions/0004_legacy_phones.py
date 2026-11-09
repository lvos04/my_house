"""legacy phone tokens: expiry date and a limited role

Phones paired before migration 0002 kept full admin rights (``api_clients.role`` server default
"admin") and had no expiry date: the 180-day clock only started at the next use of the token, so a
token that is never used again never expired and stayed a full admin (verbeterlijst 18).

- Every token without an expiry date gets one: 180 days after its last use (or pairing), but at
  least 30 days from now, so a phone that is still in use can renew it (POST /clients/self/refresh).
- Those same legacy tokens drop from role "admin" to "control" (operate devices, not manage the
  system), the default for new pairings. Re-pair the phone with role admin when that is needed.
  Each change gets an audit-log line (action ``legacy_phone_limited``), which also lets the
  downgrade find them.
- The server default of ``api_clients.role`` becomes "control".

Downgrade restores the server default "admin" and gives the limited tokens their admin role
back (found through the audit-log lines). The expiry dates stay: schema 0003 understands them.

Both periods count as elapsed time (hours), like CLIENT_TTL in deps.py (180 x 24 h). With
``interval '180 days'`` PostgreSQL keeps the wall-clock time of the session time zone, so on a
database outside UTC (compose gives it TZ=Europe/Amsterdam) the date moved an hour across a
daylight-saving change and the result depended on the server setting.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-09
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Flag first (the audit line keeps the old values), then change the rows. 4320 hours = 180 days and
    # 720 hours = 30 days, counted in hours so they are elapsed time in every session time zone.
    op.execute(
        """
        INSERT INTO audit_log (at, actor, action, target, detail)
        SELECT now(), 'systeem (database-update 0004)', 'legacy_phone_limited', left(c.name, 200),
               jsonb_build_object(
                   'client', c.id::text,
                   'old_role', c.role,
                   'new_role', CASE WHEN c.role = 'admin' THEN 'control' ELSE c.role END,
                   'expires_at', greatest(coalesce(c.last_used_at, c.created_at) + interval '4320 hours',
                                          now() + interval '720 hours')
               )
        FROM api_clients AS c
        WHERE c.expires_at IS NULL AND c.revoked_at IS NULL
        """
    )
    op.execute(
        """
        UPDATE api_clients
        SET expires_at = greatest(coalesce(last_used_at, created_at) + interval '4320 hours',
                                  now() + interval '720 hours'),
            role = CASE WHEN role = 'admin' THEN 'control' ELSE role END
        WHERE expires_at IS NULL AND revoked_at IS NULL
        """
    )
    # Revoked tokens never authenticate again; only give them a date so no row is left open-ended.
    op.execute("UPDATE api_clients SET expires_at = revoked_at WHERE expires_at IS NULL")
    op.alter_column("api_clients", "role", server_default="control")


def downgrade() -> None:
    op.alter_column("api_clients", "role", server_default="admin")
    op.execute(
        """
        UPDATE api_clients AS c
        SET role = 'admin'
        FROM audit_log AS a
        WHERE a.action = 'legacy_phone_limited'
          AND a.detail->>'client' = c.id::text
          AND a.detail->>'old_role' = 'admin'
          AND c.role = 'control'
        """
    )
