import hashlib
import hmac
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (db.CheckConstraint("role IN ('admin', 'user')", name="valid_role"),)
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="user")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    devices = db.relationship("Device", back_populates="owner", cascade="all, delete-orphan")
    vehicles = db.relationship("Vehicle", backref="owner", cascade="all, delete-orphan")

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def to_dict(self) -> dict:
        return {"id": self.id, "full_name": self.full_name, "email": self.email, "role": self.role}


class Device(db.Model):
    __tablename__ = "devices"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    device_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    device_name = db.Column(db.String(100), nullable=False)
    vehicle_name = db.Column(db.String(100), nullable=False)
    ecu_type = db.Column(db.String(100), nullable=False)
    token_hash = db.Column(db.String(64), nullable=False)
    firmware_version = db.Column(db.String(100))
    hardware_version = db.Column(db.String(100))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    paired_at = db.Column(db.DateTime(timezone=True))
    last_seen_at = db.Column(db.DateTime(timezone=True))
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    owner = db.relationship("User", back_populates="devices")
    sessions = db.relationship("RideSession", back_populates="device", cascade="all, delete-orphan")
    telemetry_records = db.relationship("TelemetryRecord", back_populates="device", cascade="all, delete-orphan")
    pairing_codes = db.relationship("DevicePairingCode", back_populates="paired_device", foreign_keys="DevicePairingCode.device_id")

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def set_token(self, token: str) -> None:
        self.token_hash = self.hash_token(token)

    def check_token(self, token: str) -> bool:
        return hmac.compare_digest(self.token_hash, self.hash_token(token))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "device_id": self.device_id,
            "device_name": self.device_name,
            "vehicle_name": self.vehicle_name,
            "ecu_type": self.ecu_type,
            "firmware_version": self.firmware_version,
            "hardware_version": self.hardware_version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "paired_at": self.paired_at.isoformat() if self.paired_at else None,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "is_active": self.is_active,
        }


class DevicePairingCode(db.Model):
    __tablename__ = "device_pairing_codes"
    __table_args__ = (
        db.UniqueConstraint("code_hash", name="uq_device_pairing_codes_code_hash"),
        db.Index("ix_device_pairing_codes_user_expires", "user_id", "expires_at"),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    code_hash = db.Column(db.String(64), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    attempt_count = db.Column(db.Integer, nullable=False, default=0)
    device_id = db.Column(db.Integer, db.ForeignKey("devices.id", ondelete="SET NULL"))

    owner = db.relationship("User", backref=db.backref("pairing_codes", cascade="all, delete-orphan"))
    paired_device = db.relationship("Device", back_populates="pairing_codes", foreign_keys=[device_id])

    @staticmethod
    def hash_code(code: str) -> str:
        normalized = "".join(str(code).upper().replace("-", " ").split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @property
    def is_used(self) -> bool:
        return self.used_at is not None

    @property
    def is_expired(self) -> bool:
        expires_at = self.expires_at.replace(tzinfo=timezone.utc) if self.expires_at.tzinfo is None else self.expires_at
        return utc_now() >= expires_at


class RideSession(db.Model):
    __tablename__ = "ride_sessions"
    __table_args__ = (
        db.UniqueConstraint("device_id", "session_id", name="uq_ride_sessions_device_session"),
        db.Index("ix_ride_sessions_device_started", "device_id", "started_at"),
    )
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    session_id = db.Column(db.String(100), nullable=False)
    started_at = db.Column(db.DateTime(timezone=True), nullable=False)
    ended_at = db.Column(db.DateTime(timezone=True))
    last_record_at = db.Column(db.DateTime(timezone=True), nullable=False)
    record_count = db.Column(db.Integer, nullable=False, default=0)
    first_seq = db.Column(db.BigInteger)
    last_seq = db.Column(db.BigInteger)
    sync_status = db.Column(db.String(20), nullable=False, default="syncing")
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    device = db.relationship("Device", back_populates="sessions")
    telemetry_records = db.relationship("TelemetryRecord", back_populates="ride_session")
    sync_batches = db.relationship("SyncBatch", back_populates="ride_session")


class TelemetryRecord(db.Model):
    __tablename__ = "telemetry_records"
    __table_args__ = (
        db.UniqueConstraint("device_id", "session_id", "seq", name="uq_telemetry_device_session_seq"),
        db.Index("ix_telemetry_device_timestamp", "device_id", "timestamp"),
        db.Index("ix_telemetry_session_timestamp", "ride_session_id", "timestamp"),
    )
    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    ride_session_id = db.Column(db.Integer, db.ForeignKey("ride_sessions.id", ondelete="CASCADE"), nullable=False)
    session_id = db.Column(db.String(100), nullable=False)
    seq = db.Column(db.BigInteger, nullable=False)
    timestamp = db.Column(db.DateTime(timezone=True))
    device_time_ms = db.Column(db.BigInteger)
    server_received_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    rpm = db.Column(db.Float)
    tps = db.Column(db.Float)
    ect = db.Column(db.Float)
    iat = db.Column(db.Float)
    battery = db.Column(db.Float)
    injector_ms = db.Column(db.Float)
    ignition_deg = db.Column(db.Float)
    raw_frame = db.Column(db.JSON)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    device = db.relationship("Device", back_populates="telemetry_records")
    ride_session = db.relationship("RideSession", back_populates="telemetry_records")

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "seq": self.seq,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "device_time_ms": self.device_time_ms,
            "server_received_at": self.server_received_at.isoformat() if self.server_received_at else None,
            "rpm": self.rpm, "tps": self.tps, "ect": self.ect, "iat": self.iat,
            "battery": self.battery, "injector_ms": self.injector_ms,
            "ignition_deg": self.ignition_deg, "raw_frame": self.raw_frame,
        }


class SyncBatch(db.Model):
    __tablename__ = "sync_batches"
    __table_args__ = (
        db.Index("ix_sync_batches_device_received", "device_id", "received_at"),
        db.Index("ix_sync_batches_session_received", "ride_session_id", "received_at"),
    )
    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    ride_session_id = db.Column(db.Integer, db.ForeignKey("ride_sessions.id", ondelete="CASCADE"), nullable=False)
    batch_id = db.Column(db.String(100))
    first_seq = db.Column(db.BigInteger)
    last_seq = db.Column(db.BigInteger)
    received_count = db.Column(db.Integer, nullable=False)
    inserted_count = db.Column(db.Integer, nullable=False)
    duplicate_count = db.Column(db.Integer, nullable=False)
    received_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    status = db.Column(db.String(20), nullable=False, default="accepted")

    device = db.relationship("Device")
    ride_session = db.relationship("RideSession", back_populates="sync_batches")


class Vehicle(db.Model):
    __tablename__ = "vehicles"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    make = db.Column(db.String(50), nullable=False)
    model = db.Column(db.String(50), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    license_plate = db.Column(db.String(20))
    vin = db.Column(db.String(17))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    scans = db.relationship("Scan", backref="vehicle", cascade="all, delete-orphan")
    maintenance = db.relationship("Maintenance", backref="vehicle", cascade="all, delete-orphan")

    def to_dict(self):
        return {key: getattr(self, key) for key in ("id", "make", "model", "year", "license_plate", "vin")}


class Scan(db.Model):
    __tablename__ = "scans"
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False)
    scanned_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    battery_voltage = db.Column(db.Float)
    alternator_voltage = db.Column(db.Float)
    temperature = db.Column(db.Float)
    fuel_instant = db.Column(db.Float)
    fuel_avg = db.Column(db.Float)
    odometer = db.Column(db.Float)
    alerts = db.Column(db.Text)

    def to_dict(self):
        result = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        result["scanned_at"] = self.scanned_at.isoformat()
        return result


class Maintenance(db.Model):
    __tablename__ = "maintenance"
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    type = db.Column(db.String(100), nullable=False)
    notes = db.Column(db.Text)
    mileage = db.Column(db.Float)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    def to_dict(self):
        return {"id": self.id, "date": self.date.isoformat(), "type": self.type, "notes": self.notes, "mileage": self.mileage}
