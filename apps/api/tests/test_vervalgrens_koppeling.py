"""Lokale PostgreSQL-proeven voor de vervalgrens van koppelingen."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import DateTime, func, literal, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from shc_api import backup, cli
from shc_api.db import get_session, sessionmaker
from shc_api.models import MAX_CLIENT_EXPIRY, ApiClient, User
from shc_api.routers.pairing import client_view
from shc_api.security import new_token, token_hash

from .test_ws_backup_format import PW, temp_db


async def test_bovengrens_vervaltijd_is_eindig_in_postgresql():
    grens = literal(MAX_CLIENT_EXPIRY, type_=DateTime(timezone=True))
    async with sessionmaker()() as db:
        assert await db.scalar(select(func.isfinite(grens))) is True


async def test_vervalgrens_na_upgrade_en_applicatieherstel(app, monkeypatch, tmp_path):
    gebruiker = uuid.uuid4()
    aangemaakt = datetime.now(UTC) - timedelta(days=2)
    proefgevallen = {
        "maximumdatum": (aangemaakt.isoformat(), "9999-12-31 23:59:59.999999+00", 401),
        "eindige bovengrens": (aangemaakt.isoformat(), "9999-12-31 23:59:59.999998+00", 200),
        "oude koppeling": (aangemaakt.isoformat(), None, 200),
        "onbepaalde aanmaakdatum": ("infinity", None, 401),
        "onbegrensde koppeling": (aangemaakt.isoformat(), "infinity", 401),
    }
    tokens = {naam: new_token() for naam in proefgevallen}
    async with temp_db("0007") as url:
        engine = create_async_engine(url, poolclass=NullPool)

        async def proefdatabase():
            async with AsyncSession(engine) as db:
                yield db

        monkeypatch.setitem(app.dependency_overrides, get_session, proefdatabase)
        try:
            async with engine.begin() as db:
                await db.execute(User.__table__.insert().values(
                    id=gebruiker, username="tijdproef", role="viewer", password_hash=uuid.uuid4().hex,
                ))
                # Bind als tekst om de PostgreSQL-grenswaarden letterlijk op te slaan.
                await db.execute(text("""
                    INSERT INTO api_clients (id, user_id, name, token_hash, role, created_at, expires_at)
                    VALUES (:id, :user, :naam, :hash, 'viewer',
                            CAST(CAST(:aangemaakt AS text) AS timestamptz),
                            CAST(CAST(:vervalt AS text) AS timestamptz))
                """), [
                    {
                        "id": uuid.uuid4(), "user": gebruiker, "naam": naam, "hash": token_hash(tokens[naam]),
                        "aangemaakt": aanmaakdatum, "vervalt": vervaldatum,
                    }
                    for naam, (aanmaakdatum, vervaldatum, _) in proefgevallen.items()
                ])

            async def meet_verval():
                now = datetime.now(UTC)
                query = select(ApiClient, ApiClient.has_valid_expiry(now), func.isfinite(ApiClient.expires_at))
                async with AsyncSession(engine) as db:
                    waarden = {k.name: (k, geldig, eindig) for k, geldig, eindig in await db.execute(query)}
                assert waarden.keys() == proefgevallen.keys()
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    for naam, (_, vervaldatum, status) in proefgevallen.items():
                        koppeling, geldig, eindig = waarden[naam]
                        antwoord = await client.get(
                            "/api/v1/clients/self", headers={"Authorization": f"Bearer {tokens[naam]}"},
                        )
                        assert antwoord.status_code == status, naam
                        assert geldig is (status == 200), naam
                        assert client_view(koppeling)["expired"] is (status == 401), naam
                        if status == 401:
                            assert antwoord.json()["detail"]["code"] == "token_expired"
                        if vervaldatum is None:
                            assert eindig is True and koppeling.expires_at < MAX_CLIENT_EXPIRY, naam
                assert waarden["oude koppeling"][0].expires_at == aangemaakt + timedelta(days=180)

            dump = await asyncio.to_thread(cli.migrate, url, backup_dir=tmp_path)
            assert dump is not None and dump.is_file()
            await meet_verval()

            # Simuleer een archief op schema 0008 dat nog ongedateerde koppelingen bevat.
            async with engine.begin() as db:
                await db.execute(text("""
                    UPDATE api_clients SET expires_at = NULL
                    WHERE name IN ('oude koppeling', 'onbepaalde aanmaakdatum')
                """))
            archief = await backup.run_backup(PW, True, url=url, backup_dir=tmp_path)
            herstel = await backup.restore_file(tmp_path / archief["filename"], PW, url=url)
            assert not herstel.migrated and herstel.counts["api_clients"] == len(proefgevallen)
            await meet_verval()
        finally:
            await engine.dispose()
