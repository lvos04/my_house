"""Database model (plan §3). UUIDs for logical objects, UTC timestamps, IPs never as keys."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB, datetime: DateTime(timezone=True)}


def pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


# DB-side defaults for columns added after the initial schema (migration 0002 and later), so rows
# written by raw SQL or restored from older backups still get a value.
JSON_OBJECT = text("'{}'::jsonb")
JSON_ARRAY = text("'[]'::jsonb")
FALSE = text("false")
TRUE = text("true")


# --- accounts -----------------------------------------------------------------


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = pk()
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="admin")  # admin | viewer
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None]
    password_changed_at: Mapped[datetime | None]


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256(token)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]


class ApiClient(Base):
    """A paired phone. Token stored hashed; revocable."""

    __tablename__ = "api_clients"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    # admin | control | viewer. New pairings default to "control"; phones paired before
    # migration 0002 were full admins and kept that (server default "admin").
    role: Mapped[str] = mapped_column(String(16), default="control", server_default="admin")
    expires_at: Mapped[datetime | None]
    refreshed_at: Mapped[datetime | None]


class PairingCode(Base):
    __tablename__ = "pairing_codes"
    __table_args__ = (CheckConstraint("role IN ('admin', 'control', 'viewer')", name="ck_pairing_codes_role"),)
    id: Mapped[uuid.UUID] = pk()
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    label: Mapped[str | None] = mapped_column(String(100))
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    # Role the phone gets when it redeems this code (migration 0003).
    role: Mapped[str] = mapped_column(String(16), default="control", server_default="control")


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(String(200))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


# --- home & devices ------------------------------------------------------------


class Home(Base):
    __tablename__ = "homes"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Amsterdam")
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    active_from: Mapped[datetime] = mapped_column(default=utcnow)
    active_to: Mapped[datetime | None]  # set when archived (moved out)


class Room(Base):
    __tablename__ = "rooms"
    id: Mapped[uuid.UUID] = pk()
    home_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("homes.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    function: Mapped[str | None] = mapped_column(String(50))
    area_m2: Mapped[float | None] = mapped_column(Float)


class Integration(Base):
    """One configured adapter instance (a Hue bridge, a Gree unit, a P1 meter, Zigbee2MQTT)."""

    __tablename__ = "integrations"
    __table_args__ = (
        # One integration per physical device identity (bridge id, MAC, serial) in a home.
        Index(
            "uq_integrations_identity",
            "home_id",
            "provider",
            "identity",
            unique=True,
            postgresql_where=text("identity IS NOT NULL"),
        ),
    )
    id: Mapped[uuid.UUID] = pk()
    home_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("homes.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(100))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # host etc. — no secrets
    credentials_enc: Mapped[bytes | None] = mapped_column(LargeBinary)  # encrypted JSON
    state: Mapped[str] = mapped_column(String(16), default="not_configured")
    detail: Mapped[str] = mapped_column(Text, default="")
    last_ok_at: Mapped[datetime | None]
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    identity: Mapped[str | None] = mapped_column(String(128))  # stable id of the paired device
    diagnostics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=JSON_OBJECT)


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("home_id", "provider", "stable_identity"),)
    id: Mapped[uuid.UUID] = pk()
    home_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("homes.id", ondelete="CASCADE"), index=True)
    integration_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("integrations.id", ondelete="SET NULL"))
    room_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rooms.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(100))
    stable_identity: Mapped[str] = mapped_column(String(128))
    model: Mapped[str | None] = mapped_column(String(100))
    firmware: Mapped[str | None] = mapped_column(String(64))
    capabilities: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    compatibility: Mapped[str] = mapped_column(String(16), default="untested")
    lifecycle: Mapped[str] = mapped_column(String(16), default="active")  # active | replaced | archived
    online: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen: Mapped[datetime | None]
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    discovered_at: Mapped[datetime] = mapped_column(default=utcnow)
    calibration: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=JSON_OBJECT)
    diagnostics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=JSON_OBJECT)
    tested_at: Mapped[datetime | None]


class LogicalRole(Base):
    """Stable function in a home (living_room_climate, boiler_energy). Devices bind to it over time."""

    __tablename__ = "logical_roles"
    __table_args__ = (UniqueConstraint("home_id", "key"),)
    id: Mapped[uuid.UUID] = pk()
    home_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("homes.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(100))
    # Protected roles can never be switched (boiler). Enforced by policy engine.
    protected: Mapped[bool] = mapped_column(Boolean, default=False)


class DeviceBinding(Base):
    __tablename__ = "device_bindings"
    id: Mapped[uuid.UUID] = pk()
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("logical_roles.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    effective_from: Mapped[datetime] = mapped_column(default=utcnow)
    effective_to: Mapped[datetime | None]


class SensorReading(Base):
    __tablename__ = "sensor_readings"
    __table_args__ = (
        Index("ix_readings_device_metric_time", "device_id", "metric", "observed_at"),
        Index("ix_readings_observed_at", "observed_at"),  # retention deletes
        UniqueConstraint("device_id", "event_id", name="uq_readings_event"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    metric: Mapped[str] = mapped_column(String(48))
    value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(16))
    quality: Mapped[str] = mapped_column(String(12), default="measured")
    observed_at: Mapped[datetime]
    received_at: Mapped[datetime] = mapped_column(default=utcnow)
    event_id: Mapped[str] = mapped_column(String(160))
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


# --- climate -----------------------------------------------------------------


class ComfortProfile(Base):
    """A named comfort setting (target, band, mode, energy priority, schedule) a zone can follow."""

    __tablename__ = "comfort_profiles"
    __table_args__ = (UniqueConstraint("home_id", "name"),)
    id: Mapped[uuid.UUID] = pk()
    home_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("homes.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(64))
    target_c: Mapped[float] = mapped_column(Float)
    band_c: Mapped[float] = mapped_column(Float, default=0.5, server_default="0.5")
    mode: Mapped[str | None] = mapped_column(String(8))
    energy_priority: Mapped[str] = mapped_column(String(12), default="balanced", server_default="balanced")
    schedule: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default=JSON_ARRAY)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())


class ClimateZone(Base):
    __tablename__ = "climate_zones"
    id: Mapped[uuid.UUID] = pk()
    home_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("homes.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    room_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rooms.id", ondelete="SET NULL"))
    unit_device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    sensor_device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    outdoor_group: Mapped[str | None] = mapped_column(String(64))  # zones sharing one outdoor unit
    profile: Mapped[str | None] = mapped_column(String(64), default="Comfort")
    policy: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # control state
    control: Mapped[str] = mapped_column(String(16), default="schedule")  # schedule|manual|off|oem_fallback
    desired_mode: Mapped[str] = mapped_column(String(8), default="off")
    desired_target_c: Mapped[float | None] = mapped_column(Float)
    override_until: Mapped[datetime | None]
    override_source: Mapped[str | None] = mapped_column(String(16))
    last_command_at: Mapped[datetime | None]
    last_commanded: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    start_times: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    adaptive_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # command tracking: the command in flight and consecutive unit/command mismatches
    pending_command_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    pending_since: Mapped[datetime | None]
    mismatch_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # setpoint correction (°C, unrounded) from the external sensor, kept apart from what is sent to the unit
    correction_c: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("comfort_profiles.id", ondelete="SET NULL"))
    last_decision: Mapped[str | None] = mapped_column(String(32))
    watchdog_since: Mapped[datetime | None]


class ScheduleBlock(Base):
    __tablename__ = "schedule_blocks"
    id: Mapped[uuid.UUID] = pk()
    zone_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("climate_zones.id", ondelete="CASCADE"), index=True)
    days: Mapped[list[Any]] = mapped_column(JSONB)  # 0=maandag … 6=zondag
    start: Mapped[str] = mapped_column(String(5))  # HH:MM local time
    target_c: Mapped[float] = mapped_column(Float)
    mode: Mapped[str | None] = mapped_column(String(8))  # None = keep current mode
    label: Mapped[str] = mapped_column(String(50), default="")


class ZoneLog(Base):
    __tablename__ = "zone_log"
    # (zone_id, at) serves every lookup by zone; "at" alone serves the retention delete (0003).
    __table_args__ = (Index("ix_zone_log_zone_at", "zone_id", "at"), Index("ix_zone_log_at", "at"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    zone_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("climate_zones.id", ondelete="CASCADE"))
    at: Mapped[datetime] = mapped_column(default=utcnow)
    kind: Mapped[str] = mapped_column(String(24))
    message: Mapped[str] = mapped_column(Text)


class AdaptiveModelVersion(Base):
    __tablename__ = "adaptive_models"
    id: Mapped[uuid.UUID] = pk()
    zone_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("climate_zones.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=False)


class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[uuid.UUID] = pk()
    home_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("homes.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    variants: Mapped[list[Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    zone_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("climate_zones.id", ondelete="SET NULL"))
    current_variant: Mapped[int | None] = mapped_column(Integer)  # index into variants while running
    auto_apply: Mapped[bool] = mapped_column(Boolean, default=False, server_default=FALSE)
    paused_reason: Mapped[str | None] = mapped_column(Text)


# --- commands, buttons ---------------------------------------------------------


class CommandRecord(Base):
    __tablename__ = "commands"
    id: Mapped[uuid.UUID] = pk()
    home_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("homes.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    zone_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("climate_zones.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(32))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String(16))  # user | schedule | engine | button
    actor: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(12), default="queued", index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class ButtonMapping(Base):
    __tablename__ = "button_mappings"
    id: Mapped[uuid.UUID] = pk()
    home_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("homes.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    button: Mapped[str] = mapped_column(String(64))  # Hue v2 resource ids are 36 chars
    event: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(12))  # lights | climate | combined
    action: Mapped[dict[str, Any]] = mapped_column(JSONB)
    combined_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=TRUE)


class ProcessedEvent(Base):
    """Dedupe ledger for push events (button presses etc.). Short-lived: pruned by the worker, not backed up."""

    __tablename__ = "processed_events"
    event_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now(), index=True)


# --- energy ------------------------------------------------------------------


class Tariff(Base):
    __tablename__ = "tariffs"
    __table_args__ = (UniqueConstraint("home_id", "valid_from", name="uq_tariffs_home_valid_from"),)
    id: Mapped[uuid.UUID] = pk()
    home_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("homes.id", ondelete="CASCADE"), index=True)
    valid_from: Mapped[date] = mapped_column(Date)
    import_per_kwh: Mapped[float] = mapped_column(Numeric(8, 5))
    export_per_kwh: Mapped[float] = mapped_column(Numeric(8, 5), default=0, server_default="0")
    fixed_per_day: Mapped[float] = mapped_column(Numeric(8, 5), default=0, server_default="0")
    source: Mapped[str] = mapped_column(String(32), default="handmatig")
    # Optional day (T1) / night (T2) import prices; import_per_kwh stays the single-rate price.
    import_t1_per_kwh: Mapped[float | None] = mapped_column(Numeric(8, 5))
    import_t2_per_kwh: Mapped[float | None] = mapped_column(Numeric(8, 5))


class EnergyHourly(Base):
    """Hourly energy per device/metric, aggregated by the worker from raw counter readings. Kept forever."""

    __tablename__ = "energy_hourly"
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)
    metric: Mapped[str] = mapped_column(String(48), primary_key=True)
    hour_start: Mapped[datetime] = mapped_column(primary_key=True, index=True)
    kwh: Mapped[float] = mapped_column(Float)
    first_value: Mapped[float | None] = mapped_column(Float)
    last_value: Mapped[float | None] = mapped_column(Float)
    samples: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    flags: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default=JSON_ARRAY)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, server_default=func.now())


# --- operations ----------------------------------------------------------------


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_open", "home_id", "resolved_at"),)
    id: Mapped[uuid.UUID] = pk()
    home_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("homes.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(160))  # dedupe key, one open alert per key
    severity: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    acknowledged_at: Mapped[datetime | None]
    resolved_at: Mapped[datetime | None]


class BackupRecord(Base):
    __tablename__ = "backups"
    id: Mapped[uuid.UUID] = pk()
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    kind: Mapped[str] = mapped_column(String(16))  # export | restore_test
    filename: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(12), default="ok")
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    auto: Mapped[bool] = mapped_column(Boolean, default=False, server_default=FALSE)  # made by the worker
    app_version: Mapped[str | None] = mapped_column(String(32))
    schema_revision: Mapped[str | None] = mapped_column(String(64))
