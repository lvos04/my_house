"""Runtime configuration. Everything comes from environment variables (prefix SHC_)
or Docker secrets files, so the same image runs on TrueNAS or any Linux host."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

VERSION = "0.1.0"


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

    # Cookie "Secure" flag: on when served over HTTPS (tunnel or reverse proxy).
    cookie_secure: bool = True
    session_hours: int = 24 * 14
    login_max_attempts: int = 5
    login_window_s: int = 300

    worker_poll_interval_s: int = 15
    climate_interval_s: int = 60
    mqtt_host: str = "mosquitto"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password_file: Path | None = None

    log_level: str = "INFO"
    trusted_proxies: list[str] = Field(default_factory=lambda: ["127.0.0.1", "172.16.0.0/12", "10.0.0.0/8"])

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
