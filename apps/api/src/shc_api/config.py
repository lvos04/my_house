"""Runtime configuration. Everything comes from environment variables (prefix SHC_)
or Docker secrets files, so the same image runs on TrueNAS or any Linux host."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,31}$")


def _read_version() -> str:
    """App version from the single source of truth: the repository's VERSION file.

    Looked up upward from this package (development checkout) and at /app/VERSION (container);
    falls back to the installed package metadata, then "0.0.0".
    """
    here = Path(__file__).resolve().parent
    for candidate in [*(d / "VERSION" for d in (here, *here.parents)), Path("/app/VERSION")]:
        try:
            value = candidate.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            continue
        if _VERSION_RE.match(value):
            return value
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("shc-api")
    except PackageNotFoundError:
        return "0.0.0"


VERSION = _read_version()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SHC_", env_file=None, extra="ignore")

    database_url: str = "postgresql+asyncpg://shc@localhost:5432/shc"
    # Password from a Docker secret file, injected into database_url when set.
    database_password_file: Path | None = None
    # Key used to encrypt device credentials at rest. Either the value itself or a file
    # (Docker secret). Must be 32+ random bytes, base64 or hex.
    secret_key: str | None = None
    secret_key_file: Path | None = None

    data_dir: Path = Path("/data")
    backup_dir: Path = Path("/backups")

    # Public addresses handed to phones when pairing.
    lan_url: str | None = None
    remote_url: str | None = None  # Cloudflare tunnel hostname, optional

    # Cookie "Secure" flag: "auto" (per request: only when it arrived over https, as seen
    # after trusted proxy headers), "true" or "false". Old boolean values keep working.
    cookie_secure: str = "auto"
    session_hours: int = 24 * 14
    login_max_attempts: int = 5
    login_window_s: int = 300
    # Peers whose X-Forwarded-* headers uvicorn trusts (comma-separated IPs/CIDRs, or "*").
    forwarded_allow_ips: str = "127.0.0.1"
    # Request size limits: regular API bodies, and the backup-restore upload.
    max_body_bytes: int = 262_144
    max_upload_bytes: int = 2_147_483_648

    worker_poll_interval_s: int = 15
    climate_interval_s: int = 60
    mqtt_host: str = "mosquitto"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password_file: Path | None = None

    # Raw data retention (days); hourly energy totals are kept forever.
    retention_power_days: int = 30
    retention_counter_days: int = 365
    retention_log_days: int = 90

    # Automatic backups: hour of day (local time) to run, and how many automatic backups to keep.
    backup_hour_local: int = 3
    backup_keep: int = 14

    # Reference clock for the time check; None disables the NTP comparison.
    ntp_server: str | None = "pool.ntp.org"

    log_level: str = "INFO"

    @field_validator("cookie_secure", mode="before")
    @classmethod
    def _cookie_secure(cls, v: object) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        s = str(v).strip().lower()
        if s in ("auto", ""):
            return "auto"
        if s in ("1", "true", "yes", "on"):
            return "true"
        if s in ("0", "false", "no", "off"):
            return "false"
        raise ValueError("cookie_secure moet auto, true of false zijn")

    def cookie_secure_for(self, scheme: str) -> bool:
        """Whether to set the cookie Secure flag for a request that arrived with this URL scheme."""
        if self.cookie_secure == "auto":
            return scheme == "https"
        return self.cookie_secure == "true"

    def model_post_init(self, _ctx) -> None:
        if self.database_password_file and self.database_password_file.exists():
            from urllib.parse import quote, urlsplit, urlunsplit

            pw = quote(self.database_password_file.read_text().strip(), safe="")
            u = urlsplit(self.database_url)
            user = u.username or "shc"
            host = u.hostname or "localhost"
            netloc = f"{user}:{pw}@{host}" + (f":{u.port}" if u.port else "")
            self.database_url = urlunsplit((u.scheme, netloc, u.path, u.query, u.fragment))

    def resolved_secret_key(self) -> str:
        if self.secret_key_file and self.secret_key_file.exists():
            return self.secret_key_file.read_text().strip()
        if self.secret_key:
            return self.secret_key
        raise RuntimeError(
            "SHC_SECRET_KEY of SHC_SECRET_KEY_FILE ontbreekt. Genereer er een met: "
            "python -c 'import secrets;print(secrets.token_urlsafe(48))'"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
