import json
import math
import re
import hashlib
from datetime import datetime, timezone

from flask import current_app
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from extensions import db
from models import Device, RawArtifact, RideSession, SyncBatch, TelemetryRecord, utc_now


class ValidationError(ValueError):
    def __init__(self, message: str, *, code: str = "VALIDATION_ERROR", status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


NUMERIC_FIELDS = {
    "rpm": (0, 100_000),
    "tps": (0, 100),
    "ect": (-80, 300),
    "iat": (-80, 300),
    "battery": (0, 100),
    "tps_voltage": (0, 10),
    "tps_raw_candidate": (0, 255),
    "battery_voltage": (0, 100),
    "iat_c": (-80, 300),
    "ect_c_candidate": (-80, 300),
    "map_raw": (0, 255),
    "injector_ms": (0, 1000),
    "ignition_deg": (-180, 180),
}

HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
RAW_FRAME_HEX_KEYS = {"hex", "raw_hex", "frame_hex"}
RAW_FRAME_SPLIT_RE = re.compile(r"[;,\s]+")
BOOLEAN_FIELDS = {"frame_valid", "checksum_valid", "decoder_valid"}
ESP_SOURCE_TYPE = "esp_raw"
ESP_RAW_REPRESENTATION = "legacy29_ff5"
ESP_TRANSPORT_PROFILE_ID = "honda_keihin_legacy_29"


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite drops timezone metadata; normalize loaded values before comparing."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_timestamp(value, *, field_name: str, required: bool = False) -> datetime | None:
    if value in (None, ""):
        if required:
            raise ValidationError(f"{field_name} is required")
        return None
    if isinstance(value, bool):
        raise ValidationError(f"{field_name} must be ISO 8601 or Unix epoch seconds")
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValidationError(f"{field_name} must be finite")
        try:
            parsed = datetime.fromtimestamp(value, tz=timezone.utc)
        except (ValueError, OSError, OverflowError) as exc:
            raise ValidationError(f"{field_name} is outside the supported range") from exc
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationError(f"{field_name} must be ISO 8601 or Unix epoch seconds") from exc
        if parsed.tzinfo is None:
            raise ValidationError(f"{field_name} must include a timezone")
    else:
        raise ValidationError(f"{field_name} must be ISO 8601 or Unix epoch seconds")
    return parsed.astimezone(timezone.utc)


def _string_field(payload: dict, name: str, *, maximum: int, required: bool = True) -> str | None:
    value = payload.get(name)
    if value in (None, "") and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} is required")
    value = value.strip()
    if len(value) > maximum:
        raise ValidationError(f"{name} must be {maximum} characters or fewer")
    return value


def _raw_frame_max_bytes() -> int:
    try:
        return int(current_app.config.get("RAW_FRAME_MAX_BYTES", 16_384))
    except RuntimeError:
        return 16_384


def _frame_text(value, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a hexadecimal string")
    value = value.strip()
    if not value:
        raise ValidationError(f"{field_name} must not be empty")
    if ";" in value or "," in value or any(char.isspace() for char in value):
        tokens = [token for token in RAW_FRAME_SPLIT_RE.split(value) if token]
        if not tokens:
            raise ValidationError(f"{field_name} must not be empty")
        normalized = []
        for position, token in enumerate(tokens):
            if not re.fullmatch(r"[0-9a-fA-F]{1,2}", token):
                raise ValidationError(f"{field_name} token {position} must be 1-2 hexadecimal characters")
            normalized.append(f"{int(token, 16):02X}")
        return ";".join(normalized)
    if len(value) % 2 != 0 or not HEX_RE.fullmatch(value):
        raise ValidationError(f"{field_name} must contain an even number of hexadecimal characters")
    return value.upper()


def _raw_frame(payload: dict):
    raw_frame = payload.get("raw_frame")
    if raw_frame is None:
        return None
    if isinstance(raw_frame, str):
        raw_frame = _frame_text(raw_frame, field_name="raw_frame")
    elif isinstance(raw_frame, dict):
        raw_frame = dict(raw_frame)
        if not any(raw_frame.get(key) is not None for key in RAW_FRAME_HEX_KEYS):
            raise ValidationError("raw_frame metadata must include hex, raw_hex, or frame_hex")
        for key in RAW_FRAME_HEX_KEYS:
            if key in raw_frame and raw_frame[key] is not None:
                raw_frame[key] = _frame_text(raw_frame[key], field_name=f"raw_frame.{key}")
    else:
        raise ValidationError("raw_frame must be a hexadecimal string or object metadata")

    try:
        encoded = json.dumps(raw_frame, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValidationError("raw_frame must contain valid JSON data") from exc
    if len(encoded.encode("utf-8")) > _raw_frame_max_bytes():
        raise ValidationError("raw_frame is too large")
    return raw_frame


def _raw_hex_from_frame(raw_frame) -> str | None:
    if raw_frame is None:
        return None
    if isinstance(raw_frame, str):
        return raw_frame.replace(";", "").upper()
    if isinstance(raw_frame, dict):
        for key in RAW_FRAME_HEX_KEYS:
            value = raw_frame.get(key)
            if isinstance(value, str) and value.strip():
                return _frame_text(value, field_name=f"raw_frame.{key}").replace(";", "").upper()
    return None


def _raw_length_from_hex(raw_hex: str | None) -> int | None:
    return len(raw_hex) // 2 if raw_hex else None


def _json_dict(payload: dict, name: str) -> dict | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValidationError(f"{name} must be a JSON object")
    try:
        json.dumps(value, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} must contain valid JSON data") from exc
    return dict(value)


def _float_field(payload: dict, name: str, *, minimum: float | None = None) -> float | None:
    value = payload.get(name)
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValidationError(f"{name} must be a finite number")
    value = float(value)
    if minimum is not None and value < minimum:
        raise ValidationError(f"{name} must be at least {minimum}")
    return value


def validate_record(payload, *, default_session_id: str | None = None) -> dict:
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

    device_time_ms = payload.get("device_time_ms")
    if device_time_ms is not None and (
        isinstance(device_time_ms, bool) or not isinstance(device_time_ms, int) or device_time_ms < 0
    ):
        raise ValidationError("device_time_ms must be a non-negative integer")

    record = {
        "session_id": session_id,
        "seq": seq,
        "timestamp": _parse_timestamp(payload.get("timestamp"), field_name="timestamp"),
        "device_time_ms": device_time_ms,
        "timestamp_ms": _float_field(payload, "timestamp_ms", minimum=0),
    }
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

    record["raw_frame"] = _raw_frame(payload)
    record["raw_hex"] = _raw_hex_from_frame(record["raw_frame"])
    record["raw_length"] = _raw_length_from_hex(record["raw_hex"])
    record["raw_representation"] = ESP_RAW_REPRESENTATION if record["raw_hex"] else None
    record["candidate_signals"] = _json_dict(payload, "candidate_signals")
    record["quality_flags"] = _json_dict(payload, "quality_flags")
    for name in BOOLEAN_FIELDS:
        value = payload.get(name)
        if value is None:
            record[name] = True
            continue
        if not isinstance(value, bool):
            raise ValidationError(f"{name} must be a boolean")
        record[name] = value
    return record


def _upload_metadata(payload: dict) -> dict:
    sample_interval_ms = _float_field(payload, "sample_interval_ms", minimum=0)
    sampling_rate_hz = _float_field(payload, "sampling_rate_hz", minimum=0)
    ecu_profile_id = payload.get("ecu_profile_id")
    if ecu_profile_id in (None, ""):
        ecu_profile_id = ESP_TRANSPORT_PROFILE_ID
    elif not isinstance(ecu_profile_id, str):
        raise ValidationError("ecu_profile_id must be a string")
    else:
        ecu_profile_id = ecu_profile_id.strip()
        if len(ecu_profile_id) > 100:
            raise ValidationError("ecu_profile_id must be 100 characters or fewer")
    return {
        "sample_interval_ms": sample_interval_ms,
        "sampling_rate_hz": sampling_rate_hz,
        "ecu_profile_id": ecu_profile_id,
    }


def validate_single_upload(payload) -> dict:
    if not isinstance(payload, dict):
        raise ValidationError("request body must be a JSON object", code="MALFORMED_JSON", status_code=400)
    record = validate_record(payload)
    metadata = _upload_metadata(payload)
    return {
        "session_id": record["session_id"],
        "session_started_at": None,
        "session_ended": False,
        "batch_id": None,
        "records": [record],
        **metadata,
    }


def validate_batch(payload) -> dict:
    if not isinstance(payload, dict):
        raise ValidationError("request body must be a JSON object", code="MALFORMED_JSON", status_code=400)
    session_id = _string_field(payload, "session_id", maximum=100)
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValidationError("records must be a non-empty array")
    if len(records) > current_app.config["MAX_LOG_RECORDS"]:
        raise ValidationError(f"records may contain at most {current_app.config['MAX_LOG_RECORDS']} items")

    session_ended = payload.get("session_ended", False)
    if not isinstance(session_ended, bool):
        raise ValidationError("session_ended must be a boolean")

    batch_id = _string_field(payload, "batch_id", maximum=100, required=False)
    started_at = _parse_timestamp(payload.get("session_started_at"), field_name="session_started_at")
    metadata = _upload_metadata(payload)

    validated = []
    for index, record in enumerate(records):
        try:
            item = validate_record(record, default_session_id=session_id)
            if item["session_id"] != session_id:
                raise ValidationError("record session_id must match the top-level session_id")
            validated.append(item)
        except ValidationError as exc:
            raise ValidationError(f"records[{index}]: {exc}", code=exc.code, status_code=exc.status_code) from exc
    return {
        "session_id": session_id,
        "session_started_at": started_at,
        "session_ended": session_ended,
        "batch_id": batch_id,
        "records": validated,
        **metadata,
    }


def _insert_do_nothing(table, values, *, index_elements: list[str]):
    dialect = db.session.get_bind().dialect.name
    if dialect == "postgresql":
        return postgres_insert(table).values(values).on_conflict_do_nothing(index_elements=index_elements)
    if dialect == "sqlite":
        return sqlite_insert(table).values(values).on_conflict_do_nothing(index_elements=index_elements)
    return table.insert().values(values)


def _unique_records(records: list[dict]) -> list[dict]:
    unique = []
    seen = set()
    for record in records:
        if record["seq"] in seen:
            continue
        seen.add(record["seq"])
        unique.append(record)
    return unique


def _event_time(record: dict) -> datetime:
    return record["timestamp"] or record["server_received_at"]


def _raw_artifact_payload(records: list[dict]) -> bytes | None:
    raw_frames = [
        {"seq": record["seq"], "raw_frame": record["raw_frame"]}
        for record in records
        if record.get("raw_frame") is not None
    ]
    if not raw_frames:
        return None
    return json.dumps(raw_frames, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _raw_transport_record(record: dict, *, now: datetime, upload: dict) -> dict:
    stored = {
        "session_id": record["session_id"],
        "seq": record["seq"],
        "timestamp": record.get("timestamp"),
        "device_time_ms": record.get("device_time_ms"),
        "timestamp_ms": record.get("timestamp_ms"),
        "server_received_at": now,
        "raw_frame": record.get("raw_frame"),
        "raw_hex": record.get("raw_hex"),
        "raw_length": record.get("raw_length"),
        "raw_representation": record.get("raw_representation"),
        "candidate_signals": {},
        "quality_flags": {
            "raw_representation": record.get("raw_representation"),
            "transport_profile_id": upload.get("ecu_profile_id"),
        },
        "frame_valid": True,
        "checksum_valid": True,
        "decoder_valid": False,
    }
    if record.get("raw_frame") is None:
        for name in NUMERIC_FIELDS:
            stored[name] = record.get(name)
        stored["candidate_signals"] = record.get("candidate_signals") or {}
        stored["quality_flags"] = record.get("quality_flags") or {}
        for name in BOOLEAN_FIELDS:
            stored[name] = record.get(name, True)
    return stored


def _sequence_ack(device: Device, session_id: str) -> dict:
    seqs = list(
        db.session.scalars(
            select(TelemetryRecord.seq)
            .where(TelemetryRecord.device_id == device.id, TelemetryRecord.session_id == session_id)
            .order_by(TelemetryRecord.seq)
        )
    )
    if not seqs:
        return {"minimum": None, "maximum": None, "contiguous_until": None}

    minimum = int(seqs[0])
    maximum = int(seqs[-1])
    expected = minimum
    contiguous_until = minimum - 1
    for seq in seqs:
        seq = int(seq)
        if seq < expected:
            continue
        if seq != expected:
            break
        contiguous_until = seq
        expected += 1
    return {"minimum": minimum, "maximum": maximum, "contiguous_until": contiguous_until}


def _refresh_session_metadata(ride_session: RideSession, *, session_ended: bool, contiguous_until: int | None) -> None:
    event_at = func.coalesce(TelemetryRecord.timestamp, TelemetryRecord.server_received_at)
    stats = db.session.execute(
        select(
            func.count(TelemetryRecord.id),
            func.min(TelemetryRecord.seq),
            func.max(TelemetryRecord.seq),
            func.min(event_at),
            func.max(event_at),
            func.count(TelemetryRecord.raw_frame),
            func.coalesce(func.sum(case((TelemetryRecord.frame_valid.is_(True), 1), else_=0)), 0),
            func.coalesce(func.sum(case((TelemetryRecord.frame_valid.is_(False), 1), else_=0)), 0),
            func.coalesce(func.sum(case((TelemetryRecord.checksum_valid.is_(False), 1), else_=0)), 0),
        ).where(TelemetryRecord.ride_session_id == ride_session.id)
    ).one()
    (
        record_count,
        first_seq,
        last_seq,
        first_event_at,
        last_event_at,
        raw_frame_count,
        valid_frame_count,
        invalid_frame_count,
        checksum_error_count,
    ) = stats
    ride_session.record_count = int(record_count or 0)
    ride_session.first_seq = first_seq
    ride_session.last_seq = last_seq
    ride_session.raw_frame_count = int(raw_frame_count or 0)
    ride_session.valid_frame_count = int(valid_frame_count or 0)
    ride_session.invalid_frame_count = int(invalid_frame_count or 0)
    ride_session.checksum_error_count = int(checksum_error_count or 0)
    if first_event_at:
        loaded_started = _as_utc(ride_session.started_at)
        ride_session.started_at = min(loaded_started, _as_utc(first_event_at)) if loaded_started else _as_utc(first_event_at)
    if last_event_at:
        ride_session.last_record_at = _as_utc(last_event_at)
        if session_ended:
            ride_session.ended_at = _as_utc(last_event_at)
    if ride_session.ended_at and last_seq is not None and contiguous_until == int(last_seq):
        ride_session.sync_status = "complete"
        ride_session.capture_status = "completed"
    elif ride_session.ended_at:
        ride_session.sync_status = "partial"
        ride_session.capture_status = "completed_with_gaps"
    else:
        ride_session.sync_status = "syncing"
        ride_session.capture_status = "capturing"
    ride_session.updated_at = utc_now()


def store_upload(device: Device, upload: dict) -> dict:
    records = upload["records"]
    unique_records = _unique_records(records)
    now = utc_now()
    received_records = [_raw_transport_record(record, now=now, upload=upload) for record in unique_records]
    event_times = [_event_time(record) for record in received_records]
    initial_started_at = upload["session_started_at"] or min(event_times)
    initial_last_at = max(event_times)

    session_values = {
        "device_id": device.id,
        "session_id": upload["session_id"],
        "started_at": initial_started_at,
        "last_record_at": initial_last_at,
        "ended_at": initial_last_at if upload["session_ended"] else None,
        "source_type": ESP_SOURCE_TYPE,
        "raw_representation": ESP_RAW_REPRESENTATION,
        "transport_profile_id": upload.get("ecu_profile_id"),
        "sample_interval_ms": upload.get("sample_interval_ms"),
        "sampling_rate_hz": upload.get("sampling_rate_hz"),
        "record_count": 0,
        "sync_status": "syncing",
        "capture_status": "capturing",
        "analysis_status": "not_requested",
        "raw_frame_count": 0,
        "valid_frame_count": 0,
        "invalid_frame_count": 0,
        "checksum_error_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    db.session.execute(
        _insert_do_nothing(
            RideSession.__table__,
            session_values,
            index_elements=["device_id", "session_id"],
        )
    )
    ride_session = RideSession.query.filter_by(device_id=device.id, session_id=upload["session_id"]).one()
    if ride_session.source_type is None:
        ride_session.source_type = ESP_SOURCE_TYPE
    if ride_session.raw_representation is None:
        ride_session.raw_representation = ESP_RAW_REPRESENTATION
    if ride_session.transport_profile_id is None:
        ride_session.transport_profile_id = upload.get("ecu_profile_id")
    if ride_session.sample_interval_ms is None and upload.get("sample_interval_ms") is not None:
        ride_session.sample_interval_ms = upload["sample_interval_ms"]
    if ride_session.sampling_rate_hz is None and upload.get("sampling_rate_hz") is not None:
        ride_session.sampling_rate_hz = upload["sampling_rate_hz"]

    values = [
        {
            **record,
            "device_id": device.id,
            "ride_session_id": ride_session.id,
            "created_at": now,
        }
        for record in received_records
    ]
    result = db.session.execute(
        _insert_do_nothing(
            TelemetryRecord.__table__,
            values,
            index_elements=["device_id", "session_id", "seq"],
        )
    )
    inserted_count = max(result.rowcount or 0, 0)
    duplicate_count = len(records) - inserted_count
    ack = _sequence_ack(device, upload["session_id"])
    _refresh_session_metadata(ride_session, session_ended=upload["session_ended"], contiguous_until=ack["contiguous_until"])

    sync_batch = SyncBatch(
        device_id=device.id,
        ride_session_id=ride_session.id,
        batch_id=upload["batch_id"],
        first_seq=min(record["seq"] for record in records),
        last_seq=max(record["seq"] for record in records),
        received_count=len(records),
        inserted_count=inserted_count,
        duplicate_count=duplicate_count,
        received_at=now,
        status="accepted",
    )
    db.session.add(sync_batch)
    db.session.flush()
    artifact_payload = _raw_artifact_payload(received_records)
    if artifact_payload is not None:
        db.session.add(
            RawArtifact(
                ride_session_id=ride_session.id,
                artifact_type="ecu_raw_log",
                storage_path=f"database://sync_batches/{sync_batch.id}/telemetry_records.raw_frame",
                sha256=hashlib.sha256(artifact_payload).hexdigest(),
                size_bytes=len(artifact_payload),
            )
        )
    device.last_seen_at = now
    db.session.commit()
    if upload["session_ended"] and current_app.config.get("ML_API_BASE_URL"):
        try:
            from services.analysis_submission import submit_session_to_analyzer

            submit_session_to_analyzer(ride_session.id)
        except Exception:
            current_app.logger.exception("Analyzer submission failed after session capture")
    return {
        "ok": True,
        "device_id": device.device_id,
        "session_id": upload["session_id"],
        "received_count": len(records),
        "inserted_count": inserted_count,
        "duplicate_count": duplicate_count,
        "accepted_sequences": ack,
        "server_time": utc_now().isoformat().replace("+00:00", "Z"),
    }


def store_records(device: Device, records: list[dict], *, end_session: bool = False) -> tuple[int, int]:
    """Compatibility wrapper for older callers."""
    if not records:
        raise ValidationError("records must be a non-empty array")
    session_ids = {record["session_id"] for record in records}
    if len(session_ids) != 1:
        raise ValidationError("all records in an upload must use the top-level session_id")
    ack = store_upload(
        device,
        {
            "session_id": next(iter(session_ids)),
            "session_started_at": None,
            "session_ended": end_session,
            "batch_id": None,
            "records": records,
        },
    )
    return ack["inserted_count"], ack["duplicate_count"]
