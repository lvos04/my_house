"""KLEIN-45 (vondst "hashes" uit KLEIN-30): de koppelcode is 8 tekens (~40 bit). Als ongezouten SHA-256 is een
gelekte ``pairing_codes.code_hash`` binnen de geldigheid van 10 minuten terug te rekenen. Daarom staat er nu
HMAC-SHA256 met een sleutel die met HKDF (context "shc-pairing-code-v1") uit SHC_SECRET_KEY wordt afgeleid;
nooit de serversleutel zelf. Inwisselen zoekt met dezelfde HMAC; een code die nog als kale SHA-256 is
opgeslagen werkt niet meer. Ontbreekt de sleutel of is hij te kort: 503 ``secret_key_invalid``, zonder iets
op te slaan of een code te gebruiken.
"""

import hashlib
import hmac
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import func, select

from shc_api.config import get_settings
from shc_api.db import sessionmaker
from shc_api.models import PairingCode, User
from shc_api.security import normalize_pairing_code
from shc_api.store import set_setting

LAN = "http://nas.local:8088"
TE_KORT = "KortGeheimQ7zX-9wv"  # 18 tekens: SecretBox weigert hem
TEKST = "De sleutel van deze server ontbreekt of is ongeldig; deze actie is niet mogelijk."


def _verwacht(code: str) -> str:
    """Onafhankelijk nagerekend: HKDF-SHA256(SHC_SECRET_KEY, info="shc-pairing-code-v1"), dan HMAC-SHA256."""
    sleutel = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"shc-pairing-code-v1").derive(
        get_settings().resolved_secret_key().encode()
    )
    return hmac.new(sleutel, code.encode(), hashlib.sha256).hexdigest()


@pytest.fixture
async def lan():
    async with sessionmaker()() as db:
        await set_setting(db, "lan_url", LAN)
        await db.commit()
    return LAN


def _telefoon(app, ip):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(ip, 40045)), base_url="http://test")


async def _wissel_in(app, code, ip):
    async with _telefoon(app, ip) as t:
        return await t.post("/api/v1/pairing/redeem", json={"code": code, "device_name": "klein45"})


async def _rij(label: str) -> PairingCode:
    async with sessionmaker()() as db:
        return (await db.execute(select(PairingCode).where(PairingCode.label == label))).scalar_one()


def _zet_sleutel(monkeypatch, tmp_path, soort: str) -> None:
    f = tmp_path / "secret_key"
    if soort == "te_kort":
        f.write_text(TE_KORT + "\n")
    monkeypatch.setattr(get_settings(), "secret_key_file", f)
    monkeypatch.setattr(get_settings(), "secret_key", None)


async def test_opgeslagen_hash_is_hmac_en_inwisselen_werkt(admin, home, app, lan):
    label = f"k45-{uuid.uuid4().hex[:6]}"
    r = await admin.post("/pairing/codes", json={"label": label})
    assert r.status_code == 200, r.text
    code = normalize_pairing_code(r.json()["code"])
    rij = await _rij(label)
    assert rij.code_hash != hashlib.sha256(code.encode()).hexdigest()  # geen kale SHA-256 meer
    assert rij.code_hash == _verwacht(code)
    assert len(rij.code_hash) == 64 and all(c in "0123456789abcdef" for c in rij.code_hash)
    # Nooit de serversleutel zelf als HMAC-sleutel.
    kaal = hmac.new(get_settings().resolved_secret_key().encode(), code.encode(), hashlib.sha256).hexdigest()
    assert rij.code_hash != kaal
    # Inwisselen werkt met de nieuwe hash (ook met kleine letters en zonder streepje), en maar één keer.
    r = await _wissel_in(app, code.lower().replace("-", ""), "klein45-a")
    assert r.status_code == 200, r.text
    assert r.json()["client"]["role"] == "control"
    assert (await _rij(label)).used_at is not None
    r = await _wissel_in(app, code, "klein45-a")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid_code"


async def test_code_met_kale_sha256_wordt_geweigerd(admin, home, app):
    code = "ABCD-EFGH"
    label = f"k45-oud-{uuid.uuid4().hex[:6]}"
    async with sessionmaker()() as db:
        user = (await db.execute(select(User).where(User.username == "beheer"))).scalar_one()
        db.add(
            PairingCode(
                code_hash=hashlib.sha256(code.encode()).hexdigest(),
                user_id=user.id,
                label=label,
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
                role="admin",
            )
        )
        await db.commit()
    r = await _wissel_in(app, code, "klein45-b")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid_code"
    assert (await _rij(label)).used_at is None


@pytest.mark.parametrize("soort", ["te_kort", "ontbreekt"])
async def test_maken_zonder_bruikbare_sleutel_geeft_503(admin, home, app, lan, monkeypatch, tmp_path, caplog, soort):
    async with sessionmaker()() as db:
        voor = (await db.execute(select(func.count()).select_from(PairingCode))).scalar_one()
    _zet_sleutel(monkeypatch, tmp_path, soort)
    r = await admin.post("/pairing/codes", json={"label": "k45-geen-sleutel"})
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == {"code": "secret_key_invalid", "message": TEKST}
    async with sessionmaker()() as db:
        assert (await db.execute(select(func.count()).select_from(PairingCode))).scalar_one() == voor
    for i in range(len(TE_KORT) - 5):
        assert TE_KORT[i : i + 6] not in r.text + caplog.text


@pytest.mark.parametrize("soort", ["te_kort", "ontbreekt"])
async def test_inwisselen_zonder_bruikbare_sleutel_geeft_503(
    admin, home, app, lan, monkeypatch, tmp_path, caplog, soort
):
    label = f"k45-503-{uuid.uuid4().hex[:6]}"
    r = await admin.post("/pairing/codes", json={"label": label})
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    _zet_sleutel(monkeypatch, tmp_path, soort)
    r = await _wissel_in(app, code, "klein45-c")
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == {"code": "secret_key_invalid", "message": TEKST}
    assert (await _rij(label)).used_at is None  # de code is niet verbruikt
    for i in range(len(TE_KORT) - 5):
        assert TE_KORT[i : i + 6] not in r.text + caplog.text
