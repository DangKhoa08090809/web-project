import json
import math
from datetime import datetime, timezone

from flask import current_app
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from extensions import db
from models import Device, RideSession, TelemetryRecord, utc_now


class ValidationError(ValueError):
    pass


NUMERIC_FIELDS = {
    "rpm": (0, 100_000),
    "tps": (0, 100),
    "ect": (-80, 300),
    "iat": (-80, 300),
    "battery": (0, 100),
    "injector_ms": (0, 1000),
    "ignition_deg": (-180, 180),
}


def _as_utc(value: datetime) -> datetime:
    """SQLite drops timezone metadata; normalize loaded values before comparing."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_timestamp(value) -> datetime:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            parsed = datetime.fromtimestamp(value, tz=timezone.utc)
        except (ValueError, OSError, OverflowError) as exc:
            raise ValidationError("timestamp is outside the supported range") from exc
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationError("timestamp must be ISO 8601 or Unix epoch seconds") from exc
        if parsed.tzinfo is None:
            raise ValidationError("timestamp must include a timezone")
    else:
        raise ValidationError("timestamp must be ISO 8601 or Unix epoch seconds")
    return parsed.astimezone(timezone.utc)


def validate_record(payload, *, default_session_id=None) -> dict:
    if not isinstance(payload, dict):
        raise ValidationError("each telemetry record must be a JSON object")

    session_id = payload.get("session_id", default_session_id)
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValidationError("session_id is required")
    session_id = session_id.strip()
    if len(session_id) > 100:
        raise ValidationError("session_id must be 100 characters or fewer")

    seq = payload.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        raise ValidationError("seq must be a non-negative integer")

    record = {"session_id": session_id, "seq": seq, "timestamp": _parse_timestamp(payload.get("timestamp"))}
    for name, (minimum, maximum) in NUMERIC_FIELDS.items():
        value = payload.get(name)
        if value is None:
            record[name] = None
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValidationError(f"{name} must be a finite number")
        if not minimum <= value <= maximum:
            raise ValidationError(f"{name} must be between {minimum} and {maximum}")
        record[name] = float(value)

    raw_frame = payload.get("raw_frame")
    try:
        encoded = json.dumps(raw_frame, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValidationError("raw_frame must contain valid JSON data") from exc
    if len(encoded.encode("utf-8")) > 16_384:
        raise ValidationError("raw_frame is too large")
    record["raw_frame"] = raw_frame
    return record


def validate_batch(payload) -> list[dict]:
    if not isinstance(payload, dict):
        raise ValidationError("request body must be a JSON object")
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValidationError("session_id is required")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValidationError("records must be a non-empty array")
    if len(records) > current_app.config["MAX_LOG_RECORDS"]:
        raise ValidationError(f"records may contain at most {current_app.config['MAX_LOG_RECORDS']} items")
    validated = []
    for index, record in enumerate(records):
        try:
            item = validate_record(record, default_session_id=session_id)
            if item["session_id"] != session_id.strip():
                raise ValidationError("record session_id must match the top-level session_id")
            validated.append(item)
        except ValidationError as exc:
            raise ValidationError(f"records[{index}]: {exc}") from exc
    return validated


def store_records(device: Device, records: list[dict], *, end_session: bool = False) -> tuple[int, int]:
    """Store one-session telemetry using an atomic conflict-safe bulk insert."""
    session_ids = {record["session_id"] for record in records}
    if len(session_ids) != 1:
        raise ValidationError("all records in an upload must use the top-level session_id")
    external_session_id = next(iter(session_ids))

    # Count duplicate sequence numbers within the request before asking the DB.
    unique_records = []
    seen = set()
    for record in records:
        key = record["seq"]
        if key not in seen:
            seen.add(key)
            unique_records.append(record)

    ride_session = RideSession.query.filter_by(device_id=device.id, session_id=external_session_id).first()
    timestamps = [record["timestamp"] for record in unique_records]
    if ride_session is None:
        session_values = {
            "device_id": device.id,
            "session_id": external_session_id,
            "started_at": min(timestamps),
            "last_record_at": max(timestamps),
            "ended_at": max(timestamps) if end_session else None,
            "record_count": 0,
            "created_at": utc_now(),
        }
        dialect = db.session.get_bind().dialect.name
        session_table = RideSession.__table__
        if dialect == "postgresql":
            create_session = postgres_insert(session_table).values(session_values).on_conflict_do_nothing(
                index_elements=["device_id", "session_id"]
            )
        elif dialect == "sqlite":
            create_session = sqlite_insert(session_table).values(session_values).on_conflict_do_nothing(
                index_elements=["device_id", "session_id"]
            )
        else:
            create_session = session_table.insert().values(session_values)
        db.session.execute(create_session)
        ride_session = RideSession.query.filter_by(device_id=device.id, session_id=external_session_id).one()

    now = utc_now()
    values = [
        {
            **record,
            "device_id": device.id,
            "ride_session_id": ride_session.id,
            "created_at": now,
        }
        for record in unique_records
    ]
    table = TelemetryRecord.__table__
    dialect = db.session.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgres_insert(table).values(values).on_conflict_do_nothing(
            index_elements=["device_id", "session_id", "seq"]
        )
    elif dialect == "sqlite":
        statement = sqlite_insert(table).values(values).on_conflict_do_nothing(
            index_elements=["device_id", "session_id", "seq"]
        )
    else:  # Local/test portability; production is explicitly PostgreSQL.
        statement = table.insert().values(values)

    result = db.session.execute(statement)
    inserted = max(result.rowcount or 0, 0)
    skipped = len(records) - inserted
    if inserted:
        ride_session.started_at = min(_as_utc(ride_session.started_at), min(timestamps))
        ride_session.last_record_at = max(_as_utc(ride_session.last_record_at), max(timestamps))
        if end_session:
            ride_session.ended_at = ride_session.last_record_at
        db.session.execute(
            update(RideSession)
            .where(RideSession.id == ride_session.id)
            .values(record_count=RideSession.record_count + inserted)
            .execution_options(synchronize_session=False)
        )
    device.last_seen_at = now
    db.session.commit()
    return inserted, skipped
