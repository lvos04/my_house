"""Optional push message outside the app (verbeterlijst 28): when the worker or the backups stop.

Alerts in the app reach nobody when the app itself cannot: a stopped worker sends no Android
notification. So the API (a different container) can also POST a short message to a URL of your
choice, for example an ntfy topic (``https://ntfy.sh/<geheim-onderwerp>`` or a self-hosted ntfy on
the LAN). The body is plain text; ``Title``, ``Priority`` and ``Tags`` headers follow ntfy, other
webhook receivers simply ignore them.

The URL comes from the app (Systeem › Meldingen buiten de app, stored encrypted: a topic URL is a
secret) or else from ``SHC_NOTIFY_URL``. Not set: nothing is sent. Sending never raises.
Messages are sent by ``watch.StatusWatch`` on a state change only (worker down / back, backups
failing or too old / back to normal), never repeatedly.
"""

from __future__ import annotations

import base64
import logging
from urllib.parse import urlsplit

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from .config import VERSION, get_settings
from .store import SecretSettingError, get_secret_setting

log = logging.getLogger(__name__)

SETTING_KEY = "notify_url_enc"  # SecretBox-encrypted, like backup_passphrase_enc
# KLEIN-44: vaste waarschuwing bij een ontbrekende of te korte hoofdsleutel; nooit (een deel van) de sleutel.
SLEUTEL_ONBRUIKBAAR_LOG = "Sleutel van deze server ontbreekt of is ongeldig; meldingsadres niet gelezen."
TIMEOUT_S = 10.0


def url_problem(url: str) -> str | None:
    """Dutch explanation when ``url`` cannot be a push address, else None."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return "Ongeldig adres."
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "Gebruik een volledig adres dat begint met https:// (of http:// in je thuisnetwerk)."
    if parts.username or parts.password:
        return "Zet geen gebruikersnaam of wachtwoord in het adres; gebruik een geheim onderwerp (topic)."
    return None


def url_hint(url: str | None) -> str | None:
    """What may be shown of the address: scheme and host only (the topic is the secret)."""
    if not url:
        return None
    p = urlsplit(url)
    return f"{p.scheme}://{p.hostname}{f':{p.port}' if p.port else ''}/…"


async def configured_url(db: AsyncSession) -> tuple[str | None, str | None]:
    """(url, source) with source "app" or "env"; (None, None) when not set. An app value that cannot
    be decrypted (other master key) falls back to the environment value. So does a missing or too short
    master key (KLEIN-44), with its own fixed warning without key material."""
    try:
        url = await get_secret_setting(db, SETTING_KEY)
    except SecretSettingError:
        log.warning("Push-adres kan niet ontsleuteld worden (andere hoofdsleutel); stel het opnieuw in")
        url = None
    except (RuntimeError, ValueError):  # the SecretBox cannot be made: key missing or too short
        log.warning(SLEUTEL_ONBRUIKBAAR_LOG)
        url = None
    if url:
        return str(url), "app"
    env = (get_settings().notify_url or "").strip()
    return (env, "env") if env else (None, None)


KEY_ERROR = (
    "Het push-adres dat in de app is ingesteld, past niet bij de hoofdsleutel van deze server (herstel op een "
    "nieuwe NAS of sleutelwissel) en wordt niet gebruikt. Stel het opnieuw in."
)


async def stored_url_error(db: AsyncSession) -> str | None:
    """Dutch explanation when the address stored in the app cannot be decrypted, else None."""
    try:
        await get_secret_setting(db, SETTING_KEY)
    except ValueError:
        return KEY_ERROR
    return None


def _header(value: str) -> str:
    """HTTP headers are latin-1; ntfy understands RFC 2047 for anything else."""
    try:
        value.encode("ascii")
        return value
    except UnicodeEncodeError:
        return "=?UTF-8?B?" + base64.b64encode(value.encode()).decode() + "?="


async def send(
    url: str,
    title: str,
    message: str,
    *,
    priority: str = "high",
    tags: str = "warning",
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """POST one message. Returns None on success, else a short Dutch error (never raises)."""
    if problem := url_problem(url):
        return problem
    headers = {
        "Title": _header(title),
        "Priority": priority,
        "Tags": tags,
        "User-Agent": f"smart-home-control/{VERSION}",
        "Content-Type": "text/plain; charset=utf-8",
    }
    try:
        if client is not None:
            r = await client.post(url, content=message.encode(), headers=headers, timeout=TIMEOUT_S)
        else:
            async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False) as c:
                r = await c.post(url, content=message.encode(), headers=headers)
    except httpx.HTTPError as exc:
        log.warning("Pushbericht niet verstuurd naar %s: %s", url_hint(url), type(exc).__name__)
        return "Geen verbinding met het push-adres."
    if r.status_code >= 400:
        log.warning("Pushbericht geweigerd door %s: HTTP %s", url_hint(url), r.status_code)
        return f"Het push-adres antwoordde met HTTP {r.status_code}."
    return None


async def notify(db: AsyncSession, title: str, message: str, **kw) -> str | None:
    """Send to the configured URL, if any. Returns an error text, or None (sent or nothing set)."""
    url, _source = await configured_url(db)
    if not url:
        return None
    return await send(url, title, message, **kw)
