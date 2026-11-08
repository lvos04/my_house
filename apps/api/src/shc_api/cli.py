"""Command line: `shc-api serve|worker|migrate|backup|restore|verify-backup|restore-drill|rekey|
reset-password|maintenance`."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import gzip
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

log = logging.getLogger("shc.cli")

WORKER_ALIVE_S = 90  # a heartbeat younger than this means the worker is running


class MigrationError(Exception):
    """Migration refused or failed; the message is meant for the user (Dutch)."""


def _passphrase(args) -> str:
    if getattr(args, "passphrase_stdin", False):
        return sys.stdin.readline().rstrip("\n")
    if args.passphrase_file:
        return Path(args.passphrase_file).read_text().strip()
    env = os.environ.get("SHC_BACKUP_PASSPHRASE")
    if env:
        return env
    return getpass.getpass("Wachtwoordzin back-up: ")


def _in_thread(fn):
    """Run `fn()` (which may call asyncio.run) in a fresh thread; works inside and outside an event loop."""
    out: list = []
    err: list[BaseException] = []

    def target():
        try:
            out.append(fn())
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
            err.append(exc)

    t = threading.Thread(target=target)
    t.start()
    t.join()
    if err:
        raise err[0]
    return out[0]


# --- migrate ------------------------------------------------------------------------------------


def _libpq(url: str) -> tuple[str, str | None]:
    """SQLAlchemy URL -> (libpq URL without password, password)."""
    u = urlsplit(url.replace("+asyncpg", ""))
    pw = unquote(u.password) if u.password else None
    netloc = (u.username or "") + ("@" if u.username else "") + (u.hostname or "")
    if u.port:
        netloc += f":{u.port}"
    return urlunsplit((u.scheme, netloc, u.path, u.query, u.fragment)), pw


def current_revision(url: str | None = None) -> str | None:
    """The database's alembic revision, or None for an empty database."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from .config import get_settings

    async def q():
        engine = create_async_engine(url or get_settings().database_url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                if (await conn.execute(text("SELECT to_regclass('alembic_version')"))).scalar() is None:
                    return None
                return (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()
        finally:
            await engine.dispose()

    return _in_thread(lambda: asyncio.run(q()))


def pre_migration_dump(url: str, revision: str, backup_dir: Path) -> Path | None:
    """pg_dump (gzip) before migrating (contract C11). None when pg_dump is not installed.
    Raises MigrationError when the dump fails: then the migration must not run."""
    exe = shutil.which("pg_dump")
    if not exe:
        log.warning("pg_dump niet gevonden: geen automatische database-dump vóór de migratie.")
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"pre-migrate-{revision}-{datetime.now(UTC):%Y%m%d-%H%M%S}.sql.gz"
    tmp = target.with_name("." + target.name + ".tmp")
    dsn, pw = _libpq(url)
    env = dict(os.environ)
    if pw:
        env["PGPASSWORD"] = pw
    try:
        with gzip.open(tmp, "wb", compresslevel=6) as out:
            proc = subprocess.Popen(  # noqa: S603 - fixed executable, no shell
                [exe, "--no-owner", "--no-privileges", f"--dbname={dsn}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            assert proc.stdout is not None
            while block := proc.stdout.read(1 << 20):
                out.write(block)
            err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
            code = proc.wait()
        if code != 0:
            raise MigrationError(
                "De automatische database-dump vóór de migratie is mislukt, dus de database is NIET bijgewerkt. "
                f"Melding van pg_dump: {err.strip()[:300] or code}. Los dit op of maak handmatig een back-up."
            )
        tmp.replace(target)
    except MigrationError:
        tmp.unlink(missing_ok=True)
        raise
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise MigrationError(
            f"De automatische database-dump vóór de migratie kon niet worden geschreven ({exc}). "
            "De database is NIET bijgewerkt."
        ) from exc
    log.info("Database-dump vóór migratie: %s", target)
    return target


def migrate(url: str | None = None, *, backup_dir: Path | None = None, pre_dump: bool = True) -> Path | None:
    """Upgrade the database to the newest schema. When migrations are pending on an existing database,
    a pg_dump is made first (contract C11). Errors propagate (MigrationError or the alembic error)."""
    from alembic import command

    from . import backup as bk
    from .config import get_settings

    s = get_settings()
    target_url = url or s.database_url
    dump = None
    current = current_revision(target_url)
    head = bk.head_revision()
    if pre_dump and current is not None and current != head:
        dump = pre_migration_dump(target_url, current, backup_dir or s.backup_dir)
    command.upgrade(bk.alembic_config(url), "head")
    return dump


# --- backup -------------------------------------------------------------------------------------


async def _backup(args) -> int:
    from . import backup as bk

    if args.auto:
        from .worker.backup_job import run_auto_backup

        result = await run_auto_backup()
        print(json.dumps(result, default=str) if args.json else f"Automatische back-up: {result.get('filename')}")
        return 0 if result.get("status") == "ok" else 1
    rid = uuid.UUID(args.record_id) if args.record_id else None
    try:
        rec = await bk.run_backup(_passphrase(args), not args.redact_secrets, record_id=rid)
    except bk.BackupError as e:
        if args.json:
            print(json.dumps({"status": "failed", "error": str(e)}))
        else:
            print(f"MISLUKT: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(rec, default=str))
    else:
        print(f"Back-up gemaakt: {rec['filename']} ({rec['size_bytes']} bytes, sha256 {rec['sha256']})")
        if not rec.get("zigbee2mqtt_included"):
            print(f"  Zigbee2MQTT: {rec['detail'].get('zigbee2mqtt', {}).get('detail', 'niet opgenomen')}")
    return 0


# --- restore ------------------------------------------------------------------------------------


def _print_manifest(v) -> None:
    from . import backup as bk

    m = v.manifest
    rel = bk.schema_relation(m.get("schema_revision"))
    rel_txt = {
        "same": "zelfde databaseversie",
        "older": "oudere databaseversie: wordt na herstel bijgewerkt",
        "newer": "NIEUWERE databaseversie: kan niet hersteld worden",
        "unknown": "onbekende databaseversie",
    }[rel]
    print(
        f"Back-up van {m['created_at']} (app {m.get('app_version')}, schema {m.get('schema_revision')}: {rel_txt}; "
        f"bestandsformaat {v.format_version})"
    )
    for t, info in m["tables"].items():
        print(f"  {t:24} {info['rows']:>10} records")
    if m.get("zigbee2mqtt_included"):
        print("  Zigbee2MQTT-gegevens aanwezig: herstel deze map vóór het starten van Zigbee2MQTT.")
    else:
        detail = (m.get("zigbee2mqtt") or {}).get("detail")
        print(f"  Geen (volledige) Zigbee2MQTT-gegevens{': ' + detail.rstrip('.') if detail else ''}.")
        print("  Zigbee-sensoren moeten mogelijk opnieuw gekoppeld worden.")
    if m.get("redacted") or not m.get("secrets_included", True):
        print("  Geredigeerde back-up: apparaatsleutels en wachtwoorden ontbreken (daarna reset-password nodig).")


async def worker_running(url: str | None = None) -> tuple[bool, bool]:
    """(worker heartbeat is fresh, maintenance mode is on)."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from .config import get_settings
    from .store import get_setting

    engine = create_async_engine(url or get_settings().database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as db:
            hb = await get_setting(db, "worker_heartbeat")
            maint = bool(await get_setting(db, "maintenance", False))
    except Exception:
        return False, False
    finally:
        await engine.dispose()
    fresh = False
    if hb:
        try:
            fresh = datetime.now(UTC) - datetime.fromisoformat(hb) < timedelta(seconds=WORKER_ALIVE_S)
        except ValueError:
            fresh = False
    return fresh, maint


async def set_maintenance(on: bool, url: str | None = None) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from .config import get_settings
    from .store import set_setting

    engine = create_async_engine(url or get_settings().database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as db:
            await set_setting(db, "maintenance", on)
            await db.commit()
    finally:
        await engine.dispose()


def print_report(r: dict) -> None:
    print(f"Hersteld: {r['total_records']} records (schema {r['schema_from']} -> {r['schema_to']}).")
    d = r["dropped"]
    if d["tables"] or d["columns"] or d["rows_removed_by_migration"]:
        print("Niet (volledig) overgenomen:")
        for t, n in d["tables"].items():
            print(f"  tabel {t}: {n} records (bestaat niet in deze versie)")
        for t, cols in d["columns"].items():
            print(f"  {t}: kolommen {', '.join(cols)} (bestaan niet in deze versie)")
        for t, n in d["rows_removed_by_migration"].items():
            print(f"  {t}: {n} records opgeruimd door de database-update")
    if r.get("new_home_id"):
        print(f"Oude woning(en) gearchiveerd; nieuwe woning aangemaakt ({r['new_home_id']}).")


async def _restore(args, dry_run: bool) -> int:
    from . import backup as bk

    path = Path(args.file)
    pw = _passphrase(args)
    try:
        v = await asyncio.to_thread(bk.verify_file, path, pw)
    except bk.BackupError as e:
        print(f"GEWEIGERD: {e}", file=sys.stderr)
        return 2
    _print_manifest(v)
    try:
        bk.check_restorable(v.manifest)
    except bk.BackupError as e:
        print(f"GEWEIGERD: {e}", file=sys.stderr)
        return 2
    if dry_run:
        print("Controle geslaagd (er is niets gewijzigd).")
        return 0
    running, maint = await worker_running()
    if running and not maint:
        print(
            "GEWEIGERD: de worker draait nog. Stop hem eerst (docker compose stop worker) "
            "of zet onderhoudsmodus aan met: shc-api maintenance on",
            file=sys.stderr,
        )
        return 3
    if not args.yes:
        if input("Alle huidige gegevens worden vervangen. Typ HERSTEL om door te gaan: ").strip() != "HERSTEL":
            print("Afgebroken.")
            return 1
    try:
        migrate()
    except MigrationError as e:
        print(f"GEWEIGERD: {e}", file=sys.stderr)
        return 2
    await set_maintenance(True)
    try:
        report = await bk.restore_file(path, pw, mode=args.mode, verified=v)
    except Exception as e:
        await set_maintenance(maint)
        print(f"MISLUKT (er is niets gewijzigd): {e}", file=sys.stderr)
        return 2
    if args.z2m_dir and v.manifest.get("zigbee2mqtt_included"):
        n = bk.extract_z2m(path, pw, Path(args.z2m_dir))
        print(f"Zigbee2MQTT: {n} bestanden teruggezet in {args.z2m_dir}.")
    print_report(report.as_dict())
    print("Veilige modus staat AAN: bevestig de apparaten in de app.")
    return 0


# --- restore drill --------------------------------------------------------------------------------


async def restore_drill(path: Path, passphrase: str, *, keep: bool = False) -> dict:
    """Restore into a temporary database, compare row counts and try to decrypt the integration
    credentials (and encrypted settings) with the CURRENT key. Never touches the real database."""
    import asyncpg
    from sqlalchemy import func, select, text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from . import backup as bk
    from .config import get_settings
    from .models import Base
    from .security import SecretBox
    from .store import SECRET_SETTINGS

    s = get_settings()
    v = await asyncio.to_thread(bk.verify_file, path, passphrase)
    bk.check_restorable(v.manifest)
    base = urlsplit(s.database_url)
    name = f"shc_drill_{uuid.uuid4().hex[:10]}"
    admin_dsn, pw = _libpq(urlunsplit((base.scheme, base.netloc, "/postgres", "", "")))
    drill_url = urlunsplit((base.scheme, base.netloc, f"/{name}", base.query, ""))
    conn = await asyncpg.connect(admin_dsn, password=pw)
    try:
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()
    result: dict = {"database": name, "ok": False, "tables": {}, "credentials": {}, "settings": {}}
    try:
        await asyncio.to_thread(migrate, drill_url, pre_dump=False)
        report = await bk.restore_file(path, passphrase, url=drill_url, verified=v)
        result["report"] = report.as_dict()
        engine = create_async_engine(drill_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine) as db:
                mismatches = []
                for t, info in v.manifest["tables"].items():
                    restored = report.counts.get(t)
                    table = Base.metadata.tables.get(t)
                    final = (
                        (await db.execute(select(func.count()).select_from(table))).scalar_one()
                        if table is not None
                        else None
                    )
                    expected = info["rows"] - report.dropped["rows_removed_by_migration"].get(t, 0)
                    # the restore itself adds a few settings (safe mode, restore report)
                    same = final is None or final == expected or (t == "settings" and final >= expected)
                    ok = restored == info["rows"] and same
                    result["tables"][t] = {"backup": info["rows"], "restored": restored, "now": final, "ok": ok}
                    if not ok and t not in report.dropped["tables"]:
                        mismatches.append(t)
                box = SecretBox(s.resolved_secret_key())
                ok_n, bad = 0, []
                rows = (await db.execute(text("SELECT name, provider, credentials_enc FROM integrations"))).all()
                for r in rows:
                    if not r.credentials_enc:
                        continue
                    try:
                        box.decrypt(bytes(r.credentials_enc))
                        ok_n += 1
                    except Exception:
                        bad.append(f"{r.name} ({r.provider})")
                result["credentials"] = {"ok": ok_n, "failed": bad, "total": ok_n + len(bad)}
                sbad = []
                for key in SECRET_SETTINGS:
                    raw = (
                        await db.execute(text("SELECT value FROM settings WHERE key = :k"), {"k": key})
                    ).scalar_one_or_none()
                    if raw and raw.get("v"):
                        try:
                            import base64

                            box.decrypt(base64.b64decode(raw["v"]))
                            result["settings"][key] = "ok"
                        except Exception:
                            result["settings"][key] = "failed"
                            sbad.append(key)
                result["ok"] = not mismatches and not bad and not sbad
                result["mismatches"] = mismatches
        finally:
            await engine.dispose()
    finally:
        if not keep:
            conn = await asyncpg.connect(admin_dsn, password=pw)
            try:
                await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            finally:
                await conn.close()
    return result


async def _restore_drill(args) -> int:
    from . import backup as bk

    try:
        r = await restore_drill(Path(args.file), _passphrase(args), keep=args.keep)
    except bk.BackupError as e:
        print(f"MISLUKT: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(r, default=str))
        return 0 if r["ok"] else 1
    print(f"Hersteloefening in tijdelijke database {r['database']}{' (bewaard)' if args.keep else ''}:")
    for t, info in r["tables"].items():
        mark = "ok" if info["ok"] else "VERSCHIL"
        print(f"  {t:24} back-up {info['backup']:>9}  hersteld {info['restored']!s:>9}  nu {info['now']!s:>9}  {mark}")
    c = r["credentials"]
    if c.get("total"):
        print(f"Apparaatsleutels: {c['ok']}/{c['total']} te ontsleutelen met de huidige sleutel.")
        for n in c["failed"]:
            print(f"  NIET te ontsleutelen: {n} (andere hoofdsleutel? gebruik shc-api rekey --old-key-file)")
    else:
        print("Apparaatsleutels: geen (geredigeerde back-up of niets gekoppeld).")
    print_report(r["report"])
    print("RESULTAAT: " + ("geslaagd" if r["ok"] else "NIET geslaagd"))
    return 0 if r["ok"] else 1


# --- rekey ------------------------------------------------------------------------------------------


def _decrypts(box, blob: bytes) -> bool:
    try:
        box.decrypt(blob)
    except Exception:
        return False
    return True


async def rekey(old_key: str, *, dry_run: bool = False, url: str | None = None) -> dict:
    """Re-encrypt integration credentials and encrypted settings from `old_key` to the current key."""
    import base64

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from .config import get_settings
    from .models import Integration
    from .security import SecretBox
    from .store import SECRET_SETTINGS, get_setting, set_setting

    s = get_settings()
    old, new = SecretBox(old_key), SecretBox(s.resolved_secret_key())
    out = {"rekeyed": [], "already_current": [], "failed": [], "settings": {}}
    engine = create_async_engine(url or s.database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as db:
            for integ in (await db.execute(select(Integration))).scalars():
                if not integ.credentials_enc:
                    continue
                label = f"{integ.name} ({integ.provider})"
                if _decrypts(new, integ.credentials_enc):
                    out["already_current"].append(label)
                    continue
                try:
                    data = old.decrypt(integ.credentials_enc)
                except Exception:
                    out["failed"].append(label)
                    continue
                integ.credentials_enc = new.encrypt(data)
                out["rekeyed"].append(label)
            for key in SECRET_SETTINGS:
                raw = await get_setting(db, key)
                if not raw:
                    continue
                blob = base64.b64decode(raw)
                if _decrypts(new, blob):
                    out["settings"][key] = "already_current"
                    continue
                try:
                    data = old.decrypt(blob)
                except Exception:
                    out["settings"][key] = "failed"
                    continue
                await set_setting(db, key, base64.b64encode(new.encrypt(data)).decode())
                out["settings"][key] = "rekeyed"
            if dry_run:
                await db.rollback()
            else:
                await db.commit()
    finally:
        await engine.dispose()
    return out


async def _rekey(args) -> int:
    old_key = Path(args.old_key_file).read_text().strip()
    r = await rekey(old_key, dry_run=args.dry_run)
    print(f"Opnieuw versleuteld: {len(r['rekeyed'])}; al met de huidige sleutel: {len(r['already_current'])}.")
    for k, v in r["settings"].items():
        print(f"  instelling {k}: {v}")
    for n in r["failed"]:
        print(f"  NIET te ontsleutelen met de oude sleutel: {n} (koppel dit apparaat opnieuw)")
    if args.dry_run:
        print("Proefrun: er is niets opgeslagen.")
    failed = r["failed"] or any(v == "failed" for v in r["settings"].values())
    return 1 if failed else 0


# --- users -------------------------------------------------------------------------------------------


async def reset_password(username: str, password: str, url: str | None = None) -> dict:
    """New password; logs out all web sessions and revokes the user's paired phones."""
    from sqlalchemy import delete, select, update
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from .config import get_settings
    from .models import ApiClient, Session, User
    from .security import MIN_PASSWORD_LENGTH, hash_password
    from .store import audit

    if len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"Minimaal {MIN_PASSWORD_LENGTH} tekens.")
    engine = create_async_engine(url or get_settings().database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as db:
            u = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
            if not u:
                raise SystemExit("Gebruiker niet gevonden.")
            now = datetime.now(UTC)
            u.password_hash = hash_password(password)
            u.failed_logins = 0
            u.locked_until = None
            u.password_changed_at = now
            sessions = (await db.execute(delete(Session).where(Session.user_id == u.id))).rowcount or 0
            phones = (
                await db.execute(
                    update(ApiClient)
                    .where(ApiClient.user_id == u.id, ApiClient.revoked_at.is_(None))
                    .values(revoked_at=now)
                )
            ).rowcount or 0
            await audit(db, "cli", "password_reset", username, sessions=sessions, phones_revoked=phones)
            from . import events

            notify = getattr(events, "publish_auth_revoked", None)  # closes open WebSockets (security stream)
            if notify is not None:
                await notify(db, reason="password_reset", user_id=u.id, sessions=True, clients=True)
            await db.commit()
    finally:
        await engine.dispose()
    return {"sessions": sessions, "phones_revoked": phones}


async def _reset_password(args) -> int:
    if args.password_stdin:
        pw = sys.stdin.readline().rstrip("\n")
    else:
        pw = getpass.getpass("Nieuw wachtwoord: ")
    r = await reset_password(args.username, pw)
    print(
        f"Wachtwoord gewijzigd; {r['sessions']} sessie(s) afgemeld en "
        f"{r['phones_revoked']} gekoppelde telefoon(s) ingetrokken (opnieuw koppelen via QR)."
    )
    return 0


async def _maintenance(args) -> int:
    if args.state == "status":
        running, maint = await worker_running()
        print(f"Onderhoudsmodus: {'aan' if maint else 'uit'}; worker: {'actief' if running else 'niet actief'}.")
        return 0
    await set_maintenance(args.state == "on")
    print("Onderhoudsmodus " + ("AAN: de worker pauzeert zijn werk." if args.state == "on" else "UIT."))
    return 0


# --- entry point -----------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="shc-api")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="0.0.0.0")  # noqa: S104 (inside container)
    s.add_argument("--port", type=int, default=8000)
    sub.add_parser("worker")
    sub.add_parser("migrate")
    b = sub.add_parser("backup")
    b.add_argument("--passphrase-file")
    b.add_argument("--passphrase-stdin", action="store_true")
    b.add_argument("--redact-secrets", action="store_true")
    b.add_argument("--auto", action="store_true", help="automatische back-up met de instellingen uit de app")
    b.add_argument("--record-id")
    b.add_argument("--json", action="store_true")
    for name in ("restore", "verify-backup"):
        r = sub.add_parser(name)
        r.add_argument("file")
        r.add_argument("--passphrase-file")
        r.add_argument("--passphrase-stdin", action="store_true")
        r.add_argument("--z2m-dir")
        r.add_argument("--yes", action="store_true")
        r.add_argument("--mode", choices=["same_home", "new_home"], default="same_home")
    d = sub.add_parser("restore-drill")
    d.add_argument("file")
    d.add_argument("--passphrase-file")
    d.add_argument("--passphrase-stdin", action="store_true")
    d.add_argument("--keep", action="store_true", help="tijdelijke database niet verwijderen")
    d.add_argument("--json", action="store_true")
    k = sub.add_parser("rekey")
    k.add_argument("--old-key-file", required=True)
    k.add_argument("--dry-run", action="store_true")
    rp = sub.add_parser("reset-password")
    rp.add_argument("username")
    rp.add_argument("--password-stdin", action="store_true")
    m = sub.add_parser("maintenance")
    m.add_argument("state", choices=["on", "off", "status"])
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.cmd in ("serve", "migrate"):
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
        try:
            migrate()
        except MigrationError as e:
            print(f"Database-update geweigerd: {e}", file=sys.stderr)
            raise SystemExit(2) from e
        except Exception as e:
            print(f"Database-update mislukt: {e.__class__.__name__}: {e}", file=sys.stderr)
            raise SystemExit(2) from e
    if args.cmd == "serve":
        import uvicorn

        from .config import get_settings

        uvicorn.run(
            "shc_api.main:app",
            host=args.host,
            port=args.port,
            proxy_headers=True,
            forwarded_allow_ips=get_settings().forwarded_allow_ips,
        )
        return
    if args.cmd == "migrate":
        return
    if args.cmd == "worker":
        from .worker import run

        asyncio.run(run())
        return
    handlers = {
        "backup": _backup,
        "restore": lambda a: _restore(a, dry_run=False),
        "verify-backup": lambda a: _restore(a, dry_run=True),
        "restore-drill": _restore_drill,
        "rekey": _rekey,
        "reset-password": _reset_password,
        "maintenance": _maintenance,
    }
    code = asyncio.run(handlers[args.cmd](args))
    if code:
        raise SystemExit(code)


if __name__ == "__main__":
    main()
