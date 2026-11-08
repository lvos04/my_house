"""Command line: `shc-api serve|worker|migrate|backup|restore|verify-backup|reset-password`."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path


def _passphrase(args) -> str:
    if args.passphrase_file:
        return Path(args.passphrase_file).read_text().strip()
    env = os.environ.get("SHC_BACKUP_PASSPHRASE")
    if env:
        return env
    return getpass.getpass("Wachtwoordzin back-up: ")


def migrate() -> None:
    from alembic import command
    from alembic.config import Config

    here = Path(__file__).parent / "migrations"
    cfg = Config()
    cfg.set_main_option("script_location", str(here))
    command.upgrade(cfg, "head")


async def _backup(args) -> None:
    from .db import sessionmaker
    from .routers.system import make_backup

    async with sessionmaker()() as db:
        rec = await make_backup(db, _passphrase(args), not args.redact_secrets)
        await db.commit()
        print(f"Back-up gemaakt: {rec.filename} ({rec.size_bytes} bytes, sha256 {rec.sha256})")


async def _restore(args, dry_run: bool) -> None:
    from . import backup as bk
    from .db import sessionmaker

    blob = Path(args.file).read_bytes()
    try:
        manifest, files = bk.open_archive(blob, _passphrase(args))
    except bk.BackupError as e:
        print(f"GEWEIGERD: {e}", file=sys.stderr)
        raise SystemExit(2) from e
    print(
        f"Back-up van {manifest['created_at']} (app {manifest['app_version']}, schema {manifest['schema_revision']})"
    )
    for t, info in manifest["tables"].items():
        print(f"  {t:24} {info['rows']:>8} records")
    if manifest.get("zigbee2mqtt_included"):
        print("  Zigbee2MQTT-gegevens aanwezig: herstel deze map vóór het starten van Zigbee2MQTT.")
    else:
        print("  Geen Zigbee2MQTT-gegevens: Zigbee-sensoren moeten mogelijk opnieuw gekoppeld worden.")
    if dry_run:
        print("Controle geslaagd (er is niets gewijzigd).")
        return
    if not args.yes:
        if (
            input("Alle huidige gegevens worden vervangen. Typ HERSTEL om door te gaan: ").strip()
            != "HERSTEL"
        ):
            raise SystemExit("Afgebroken.")
    migrate()
    async with sessionmaker()() as db:
        counts = await bk.restore(db, manifest, files)
        await db.commit()
    if args.z2m_dir and manifest.get("zigbee2mqtt_included"):
        target = Path(args.z2m_dir)
        for name, data in files.items():
            if name.startswith("zigbee2mqtt/"):
                p = target / name.removeprefix("zigbee2mqtt/")
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
    print(f"Hersteld: {sum(counts.values())} records. Veilige modus staat AAN: bevestig apparaten in de app.")


async def _reset_password(args) -> None:
    from sqlalchemy import select

    from .db import sessionmaker
    from .models import Session, User
    from .security import MIN_PASSWORD_LENGTH, hash_password

    pw = getpass.getpass("Nieuw wachtwoord: ")
    if len(pw) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"Minimaal {MIN_PASSWORD_LENGTH} tekens.")
    async with sessionmaker()() as db:
        u = (await db.execute(select(User).where(User.username == args.username))).scalar_one_or_none()
        if not u:
            raise SystemExit("Gebruiker niet gevonden.")
        u.password_hash = hash_password(pw)
        from sqlalchemy import delete

        await db.execute(delete(Session).where(Session.user_id == u.id))
        await db.commit()
    print("Wachtwoord gewijzigd; bestaande sessies zijn afgemeld.")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="shc-api")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="0.0.0.0")  # noqa: S104 (inside container)
    s.add_argument("--port", type=int, default=8000)
    sub.add_parser("worker")
    sub.add_parser("migrate")
    b = sub.add_parser("backup")
    b.add_argument("--passphrase-file")
    b.add_argument("--redact-secrets", action="store_true")
    for name in ("restore", "verify-backup"):
        r = sub.add_parser(name)
        r.add_argument("file")
        r.add_argument("--passphrase-file")
        r.add_argument("--z2m-dir")
        r.add_argument("--yes", action="store_true")
    rp = sub.add_parser("reset-password")
    rp.add_argument("username")
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn

        migrate()
        uvicorn.run(
            "shc_api.main:app",
            host=args.host,
            port=args.port,
            proxy_headers=True,
            forwarded_allow_ips=os.environ.get("SHC_FORWARDED_ALLOW_IPS", "127.0.0.1"),
        )
    elif args.cmd == "worker":
        from .worker import run

        asyncio.run(run())
    elif args.cmd == "migrate":
        migrate()
    elif args.cmd == "backup":
        asyncio.run(_backup(args))
    elif args.cmd in ("restore", "verify-backup"):
        asyncio.run(_restore(args, dry_run=args.cmd == "verify-backup"))
    elif args.cmd == "reset-password":
        asyncio.run(_reset_password(args))


if __name__ == "__main__":
    main()
