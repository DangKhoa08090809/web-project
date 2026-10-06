import hashlib
import json
import math
import re
from datetime import datetime
from typing import Any

from flask import current_app
from sqlalchemy import case, func, select

from extensions import db
from models import AnalysisResult, DecoderVersion, Device, EcuProfile, RideSession, SyncBatch, TelemetryRecord, utc_now
from services.telemetry import ValidationError, _insert_do_nothing, _parse_timestamp, _sequence_ack


PI_TELEMETRY_SCHEMA_VERSION = "canonical-telemetry-v2"
PI_SOURCE_TYPE = "pi_native"
PI_ECU_PROFILE_ID = "honda_keihin_71_17"
PI_DECODER_ID = "honda_keihin_71_17"
PI_DECODER_VERSION = "1.0.0"
PI_FEATURE_SCHEMA_VERSION = "ecu-window-features-v1"
PI_SIGNAL_COLUMNS = ["rpm", "tps_voltage", "tps_raw", "battery_voltage", "iat_c", "ect_c"]
PI_RAW_REPRESENTATION = "native24_table17"
PI_RAW_LENGTH = 24
PI_CAPTURE_TERMINATIONS = {
    "completed": "normal_stop",
    "interrupted": "unclean_runtime_shutdown",
}
PI_TERMINATION_REASONS = frozenset(PI_CAPTURE_TERMINATIONS.values())
HEX_RE = re.compile(r"^[0-9a-fA-F]+$")

PI_RECORD_FIELDS = {
    "rpm": (0, 100_000),
    "tps_voltage": (0, 10),
    "tps_raw": (0, 255),
    "battery_voltage": (0, 100),
    "iat_c": (-80, 300),
    "ect_c": (-80, 300),
}


def _json_fingerprint(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _json_clone(value: Any, *, field_name: str):
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name} must contain valid JSON data") from exc
    return json.loads(encoded)


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


def _exact_field(payload: dict, name: str, expected: str) -> str:
    value = _string_field(payload, name, maximum=max(100, len(expected)))
    if value != expected:
        raise ValidationError(f"{name} must be {expected}")
    return value


def _finite_number(
    payload: dict,
    name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    required: bool = True,
) -> float | None:
    value = payload.get(name)
    if value in (None, "") and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValidationError(f"{name} must be a finite number")
    value = float(value)
    if minimum is not None and value < minimum:
        raise ValidationError(f"{name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValidationError(f"{name} must be at most {maximum}")
    return value


def _integer_field(payload: dict, name: str, *, minimum: int = 0, required: bool = True) -> int | None:
    value = payload.get(name)
    if value in (None, "") and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValidationError(f"{name} must be a non-negative integer")
    return int(value)


def _boolean_field(payload: dict, name: str, *, required: bool = True, default: bool | None = None) -> bool:
    value = payload.get(name, default)
    if value is None and required:
        raise ValidationError(f"{name} is required")
    if not isinstance(value, bool):
        raise ValidationError(f"{name} must be a boolean")
    return bool(value)


def _optional_json_object(payload: dict, name: str) -> dict:
    value = payload.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValidationError(f"{name} must be a JSON object")
    return _json_clone(value, field_name=name)


def _optional_diagnostic_evidence(payload: dict, warnings: list[str]) -> dict | None:
    value = payload.get("diagnostic_evidence")
    if value is None:
        return None
    if isinstance(value, dict):
        try:
            return _json_clone(value, field_name="diagnostic_evidence")
        except ValidationError:
            pass
    warnings.append("diagnostic_evidence ignored because it was not a JSON object.")
    return None


def _json_array(payload: dict, name: str) -> list:
    value = payload.get(name)
    if not isinstance(value, list):
        raise ValidationError(f"{name} must be an array")
    return _json_clone(value, field_name=name)


def _sampling_metadata(payload: dict) -> dict:
    sampling = payload.get("sampling") or {}
    if not isinstance(sampling, dict):
        raise ValidationError("sampling must be a JSON object")
    return {
        "sample_interval_ms": _finite_number(
            {**sampling, "sample_interval_ms": payload.get("sample_interval_ms", sampling.get("sample_interval_ms"))},
            "sample_interval_ms",
            minimum=0,
            required=False,
        ),
        "sampling_rate_hz": _finite_number(
            {**sampling, "sampling_rate_hz": payload.get("sampling_rate_hz", sampling.get("sampling_rate_hz"))},
            "sampling_rate_hz",
            minimum=0,
            required=False,
        ),
    }


def _capture_termination_metadata(payload: dict) -> dict:
    status_value = payload.get("capture_status")
    reason_value = payload.get("termination_reason")
    has_status = status_value not in (None, "")
    has_reason = reason_value not in (None, "")
    if not has_status and not has_reason:
        return {"capture_status": None, "termination_reason": None}
    if not has_status or not has_reason:
        raise ValidationError(
            "capture_status and termination_reason must be supplied together",
            code="PI_CAPTURE_METADATA_INCOMPLETE",
        )

    capture_status = _string_field(payload, "capture_status", maximum=20)
    termination_reason = _string_field(payload, "termination_reason", maximum=80)
    if capture_status not in PI_CAPTURE_TERMINATIONS:
        allowed = ", ".join(sorted(PI_CAPTURE_TERMINATIONS))
        raise ValidationError(
            f"capture_status must be one of: {allowed}",
            code="PI_CAPTURE_STATUS_INVALID",
        )
    if termination_reason not in PI_TERMINATION_REASONS:
        allowed = ", ".join(sorted(PI_TERMINATION_REASONS))
        raise ValidationError(
            f"termination_reason must be one of: {allowed}",
            code="PI_TERMINATION_REASON_INVALID",
        )
    expected_reason = PI_CAPTURE_TERMINATIONS[capture_status]
    if termination_reason != expected_reason:
        raise ValidationError(
            f"termination_reason must be {expected_reason} when capture_status is {capture_status}",
            code="PI_CAPTURE_METADATA_MISMATCH",
        )
    return {"capture_status": capture_status, "termination_reason": termination_reason}


def _record_fingerprint_payload(record: dict) -> dict:
    return {
        "seq": int(record["seq"]),
        "timestamp_ms": float(record["timestamp_ms"]),
        "raw_hex": record["raw_hex"],
        "raw_length": int(record["raw_length"]),
        "rpm": float(record["rpm"]),
        "tps_voltage": float(record["tps_voltage"]),
        "tps_raw": float(record["tps_raw"]),
        "battery_voltage": float(record["battery_voltage"]),
        "iat_c": float(record["iat_c"]),
        "ect_c": float(record["ect_c"]),
        "frame_valid": bool(record["frame_valid"]),
        "checksum_valid": bool(record["checksum_valid"]),
        "candidate_signals": record.get("candidate_signals") or {},
    }


def _record_fingerprint(record: dict) -> str:
    return _json_fingerprint(_record_fingerprint_payload(record))


def _model_record_fingerprint(record: TelemetryRecord) -> str:
    return _json_fingerprint(
        {
            "seq": int(record.seq),
            "timestamp_ms": float(record.timestamp_ms),
            "raw_hex": str(record.raw_hex or ""),
            "raw_length": int(record.raw_length or 0),
            "rpm": float(record.rpm),
            "tps_voltage": float(record.tps_voltage),
            "tps_raw": float(record.tps_raw),
            "battery_voltage": float(record.battery_voltage),
            "iat_c": float(record.iat_c),
            "ect_c": float(record.ect_c),
            "frame_valid": bool(record.frame_valid),
            "checksum_valid": bool(record.checksum_valid),
            "candidate_signals": record.candidate_signals or {},
        }
    )


def _validate_pi_record(payload, *, index: int) -> dict:
    if not isinstance(payload, dict):
        raise ValidationError("each record must be a JSON object")

    seq = _integer_field(payload, "seq")
    timestamp_ms = _finite_number(payload, "timestamp_ms", minimum=0)
    raw_hex = _string_field(payload, "raw_hex", maximum=PI_RAW_LENGTH * 2)
    raw_hex = raw_hex.upper()
    if len(raw_hex) != PI_RAW_LENGTH * 2 or not HEX_RE.fullmatch(raw_hex):
        raise ValidationError(f"raw_hex must be {PI_RAW_LENGTH} bytes of hexadecimal native24_table17 data")
    raw_length = _integer_field(payload, "raw_length")
    if raw_length != PI_RAW_LENGTH:
        raise ValidationError(f"raw_length must be {PI_RAW_LENGTH}")
    record = {"seq": seq, "timestamp_ms": timestamp_ms, "raw_hex": raw_hex, "raw_length": raw_length}
    for name, (minimum, maximum) in PI_RECORD_FIELDS.items():
        record[name] = _finite_number(payload, name, minimum=minimum, maximum=maximum)
    record["frame_valid"] = _boolean_field(payload, "frame_valid")
    record["checksum_valid"] = _boolean_field(payload, "checksum_valid")
    record["candidate_signals"] = _optional_json_object(payload, "candidate_signals")
    record["quality_flags"] = _optional_json_object(payload, "quality_flags")
    record["record_fingerprint"] = _record_fingerprint(record)
    return record


def _normalize_analysis(payload: dict, upload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValidationError("analysis must be a JSON object")
    analysis_run_id = _string_field(payload, "analysis_run_id", maximum=100)
    telemetry_schema_version = _exact_field(payload, "telemetry_schema_version", PI_TELEMETRY_SCHEMA_VERSION)
    feature_schema_version = _exact_field(payload, "feature_schema_version", PI_FEATURE_SCHEMA_VERSION)
    signal_columns = _json_array(payload, "signal_columns")
    if signal_columns != PI_SIGNAL_COLUMNS:
        raise ValidationError(f"signal_columns must be {PI_SIGNAL_COLUMNS}")

    for optional_name, expected in (
        ("ecu_profile_id", PI_ECU_PROFILE_ID),
        ("decoder_id", PI_DECODER_ID),
        ("decoder_version", PI_DECODER_VERSION),
    ):
        value = payload.get(optional_name)
        if value not in (None, "") and value != expected:
            raise ValidationError(f"analysis {optional_name} must match session provenance")

    model_version = _string_field(payload, "model_version", maximum=100)
    overall_status = _string_field(payload, "overall_status", maximum=100)
    window_count = _integer_field(payload, "window_count")
    anomaly_window_count = _integer_field(payload, "anomaly_window_count")
    if anomaly_window_count > window_count:
        raise ValidationError("anomaly_window_count must be less than or equal to window_count")
    anomaly_ratio = _finite_number(payload, "anomaly_ratio", minimum=0, maximum=1)
    health_score = _finite_number(payload, "health_score", minimum=0, maximum=100, required=False)

    most_unusual_features = payload.get("most_unusual_features", [])
    if not isinstance(most_unusual_features, list):
        raise ValidationError("most_unusual_features must be an array")
    most_unusual_features = _json_clone(most_unusual_features, field_name="most_unusual_features")

    warnings = payload.get("warnings", [])
    if not isinstance(warnings, list):
        raise ValidationError("warnings must be an array")
    warnings = [str(item)[:255] for item in warnings]
    diagnostic_evidence = _optional_diagnostic_evidence(payload, warnings)

    model_loaded = payload.get("model_loaded")
    if model_loaded is not None and not isinstance(model_loaded, bool):
        raise ValidationError("model_loaded must be a boolean")

    evidence_window_count = _integer_field(payload, "evidence_window_count", required=False)
    minimum_windows_for_status = _integer_field(payload, "minimum_windows_for_status", required=False)
    evidence_sufficient = payload.get("evidence_sufficient")
    if evidence_sufficient is not None and not isinstance(evidence_sufficient, bool):
        raise ValidationError("evidence_sufficient must be a boolean")

    note = payload.get("note")
    if note is not None:
        note = str(note)[:1000]

    analyzed_at = _parse_timestamp(payload.get("analyzed_at"), field_name="analysis.analyzed_at")
    normalized = {
        "analysis_run_id": analysis_run_id,
        "session_id": upload["session_id"],
        "model_version": model_version,
        "telemetry_schema_version": telemetry_schema_version,
        "ecu_profile_id": PI_ECU_PROFILE_ID,
        "decoder_id": PI_DECODER_ID,
        "decoder_version": PI_DECODER_VERSION,
        "feature_schema_version": feature_schema_version,
        "signal_columns": signal_columns,
        "window_count": window_count,
        "anomaly_window_count": anomaly_window_count,
        "anomaly_ratio": anomaly_ratio,
        "health_score": health_score,
        "overall_status": overall_status,
        "most_unusual_features": most_unusual_features,
        "model_loaded": model_loaded,
        "warnings": warnings,
        "note": note,
        "analyzed_at": analyzed_at,
    }
    if evidence_window_count is not None:
        normalized["evidence_window_count"] = evidence_window_count
    if minimum_windows_for_status is not None:
        normalized["minimum_windows_for_status"] = minimum_windows_for_status
    if evidence_sufficient is not None:
        normalized["evidence_sufficient"] = evidence_sufficient
    if diagnostic_evidence is not None:
        normalized["diagnostic_evidence"] = diagnostic_evidence
    normalized["result_fingerprint"] = _json_fingerprint(
        {
            key: (value.isoformat() if isinstance(value, datetime) else value)
            for key, value in normalized.items()
            if key != "result_fingerprint"
        }
    )
    return normalized


def validate_pi_sync(payload) -> dict:
    if not isinstance(payload, dict):
        raise ValidationError("request body must be a JSON object", code="MALFORMED_JSON", status_code=400)
    session_id = _string_field(payload, "session_id", maximum=100)
    telemetry_schema_version = _exact_field(payload, "telemetry_schema_version", PI_TELEMETRY_SCHEMA_VERSION)
    ecu_profile_id = _exact_field(payload, "ecu_profile_id", PI_ECU_PROFILE_ID)
    decoder_id = _exact_field(payload, "decoder_id", PI_DECODER_ID)
    decoder_version = _exact_field(payload, "decoder_version", PI_DECODER_VERSION)

    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValidationError("records must be a non-empty array")
    if len(records) > current_app.config["MAX_LOG_RECORDS"]:
        raise ValidationError(f"records may contain at most {current_app.config['MAX_LOG_RECORDS']} items")

    session_ended = payload.get("session_ended", False)
    if not isinstance(session_ended, bool):
        raise ValidationError("session_ended must be a boolean")
    batch_id = _string_field(payload, "batch_id", maximum=100, required=False)

    validated_records = []
    for index, record in enumerate(records):
        try:
            validated_records.append(_validate_pi_record(record, index=index))
        except ValidationError as exc:
            raise ValidationError(f"records[{index}]: {exc}", code=exc.code, status_code=exc.status_code) from exc

    sampling = _sampling_metadata(payload)
    capture_termination = _capture_termination_metadata(payload)
    analysis = payload.get("analysis")
    if analysis is not None and not session_ended:
        raise ValidationError("analysis may only be imported with a final session_ended sync")

    upload = {
        "session_id": session_id,
        "batch_id": batch_id,
        "session_ended": session_ended,
        "telemetry_schema_version": telemetry_schema_version,
        "ecu_profile_id": ecu_profile_id,
        "decoder_id": decoder_id,
        "decoder_version": decoder_version,
        "capture_started_at": _parse_timestamp(payload.get("capture_started_at"), field_name="capture_started_at"),
        "capture_ended_at": _parse_timestamp(payload.get("capture_ended_at"), field_name="capture_ended_at"),
        "records": validated_records,
        **sampling,
        **capture_termination,
    }
    if upload["capture_started_at"] and upload["capture_ended_at"] and upload["capture_ended_at"] < upload["capture_started_at"]:
        raise ValidationError("capture_ended_at must be greater than or equal to capture_started_at")
    upload["analysis"] = _normalize_analysis(analysis, upload) if analysis is not None else None
    return upload


def _unique_pi_records(records: list[dict]) -> list[dict]:
    unique_by_seq = {}
    for record in records:
        existing = unique_by_seq.get(record["seq"])
        if existing is None:
            unique_by_seq[record["seq"]] = record
            continue
        if existing["record_fingerprint"] != record["record_fingerprint"]:
            raise ValidationError(
                f"conflicting records for seq {record['seq']} in the same batch",
                code="PI_RECORD_CONFLICT",
                status_code=409,
            )
    return list(unique_by_seq.values())


def _ensure_pi_decoder_version() -> DecoderVersion:
    ecu_profile = EcuProfile.query.filter_by(profile_key=PI_ECU_PROFILE_ID).first()
    if ecu_profile is None:
        ecu_profile = EcuProfile(
            profile_key=PI_ECU_PROFILE_ID,
            manufacturer="Honda",
            protocol_family="honda_keihin_kline",
            frame_length=24,
        )
        db.session.add(ecu_profile)
        db.session.flush()
    else:
        ecu_profile.manufacturer = "Honda"
        ecu_profile.protocol_family = "honda_keihin_kline"
        ecu_profile.frame_length = 24

    decoder_hash = hashlib.sha256(f"{PI_DECODER_ID}:{PI_DECODER_VERSION}:{PI_TELEMETRY_SCHEMA_VERSION}".encode()).hexdigest()
    decoder_version = DecoderVersion.query.filter_by(
        ecu_profile_id=ecu_profile.id,
        decoder_id=PI_DECODER_ID,
        version=PI_DECODER_VERSION,
    ).first()
    if decoder_version is None:
        decoder_version = DecoderVersion(
            ecu_profile_id=ecu_profile.id,
            decoder_id=PI_DECODER_ID,
            version=PI_DECODER_VERSION,
            schema_version=PI_TELEMETRY_SCHEMA_VERSION,
            decoder_hash=decoder_hash,
            status="production",
            notes="Pi5 native Honda Keihin 0x71 0x17 decoded canonical telemetry.",
        )
        db.session.add(decoder_version)
        db.session.flush()
    else:
        decoder_version.schema_version = PI_TELEMETRY_SCHEMA_VERSION
        decoder_version.decoder_hash = decoder_hash
        decoder_version.status = "production"
    ecu_profile.active_decoder_version_id = decoder_version.id
    ecu_profile.updated_at = utc_now()
    return decoder_version


def _initial_started_at(upload: dict, now: datetime) -> datetime:
    return upload["capture_started_at"] or now


def _reconcile_pi_capture_metadata(ride_session: RideSession, upload: dict) -> None:
    incoming_status = upload["capture_status"]
    incoming_reason = upload["termination_reason"]
    if incoming_status is None:
        return

    if ride_session.termination_reason is not None:
        if (
            ride_session.capture_status != incoming_status
            or ride_session.termination_reason != incoming_reason
        ):
            raise ValidationError(
                "capture metadata conflicts with the existing session identity",
                code="PI_CAPTURE_METADATA_CONFLICT",
                status_code=409,
            )
        return

    # Legacy rows have no termination metadata. A later authoritative report can fill it once.
    ride_session.capture_status = incoming_status
    ride_session.termination_reason = incoming_reason


def _refresh_pi_session_metadata(ride_session: RideSession, *, upload: dict, contiguous_until: int | None, now: datetime) -> None:
    stats = db.session.execute(
        select(
            func.count(TelemetryRecord.id),
            func.min(TelemetryRecord.seq),
            func.max(TelemetryRecord.seq),
            func.min(TelemetryRecord.timestamp_ms),
            func.max(TelemetryRecord.timestamp_ms),
            func.count(TelemetryRecord.raw_hex),
            func.coalesce(func.sum(case((TelemetryRecord.frame_valid.is_(True), 1), else_=0)), 0),
            func.coalesce(func.sum(case((TelemetryRecord.frame_valid.is_(False), 1), else_=0)), 0),
            func.coalesce(func.sum(case((TelemetryRecord.checksum_valid.is_(False), 1), else_=0)), 0),
        ).where(TelemetryRecord.ride_session_id == ride_session.id)
    ).one()
    (
        record_count,
        first_seq,
        last_seq,
        first_timestamp_ms,
        last_timestamp_ms,
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
    ride_session.last_record_at = now
    if first_timestamp_ms is not None and last_timestamp_ms is not None:
        ride_session.duration_ms = max(0.0, float(last_timestamp_ms) - float(first_timestamp_ms))

    if upload["capture_started_at"] is not None:
        ride_session.capture_started_at = upload["capture_started_at"]
        ride_session.started_at = upload["capture_started_at"]
    if upload["capture_ended_at"] is not None:
        ride_session.capture_ended_at = upload["capture_ended_at"]
        ride_session.ended_at = upload["capture_ended_at"]

    if upload["session_ended"] and last_seq is not None and contiguous_until == int(last_seq):
        ride_session.sync_status = "complete"
    elif upload["session_ended"]:
        ride_session.sync_status = "partial"
    else:
        ride_session.sync_status = "syncing"

    # Sync finalization describes upload completeness, not how acquisition ended.
    if ride_session.termination_reason is None:
        if upload["session_ended"] and last_seq is not None and contiguous_until == int(last_seq):
            ride_session.capture_status = "completed"
        elif upload["session_ended"]:
            ride_session.capture_status = "completed_with_gaps"
        else:
            ride_session.capture_status = "capturing"
    ride_session.updated_at = now


def _analysis_summary_payload(analysis: dict) -> dict:
    return {
        key: (value.isoformat().replace("+00:00", "Z") if isinstance(value, datetime) else value)
        for key, value in analysis.items()
        if key != "result_fingerprint"
    }


def _store_pi_analysis(ride_session: RideSession, analysis: dict | None, now: datetime) -> str | None:
    if analysis is None:
        return None

    existing = AnalysisResult.query.filter_by(analysis_run_id=analysis["analysis_run_id"]).first()
    if existing is not None:
        if existing.ride_session_id != ride_session.id:
            raise ValidationError("analysis_run_id already belongs to another session", code="PI_ANALYSIS_CONFLICT", status_code=409)
        existing_fingerprint = existing.result_fingerprint
        if existing_fingerprint is None and isinstance(existing.result_summary, dict):
            existing_fingerprint = _json_fingerprint(existing.result_summary)
        if existing_fingerprint == analysis["result_fingerprint"]:
            ride_session.analysis_status = "completed"
            ride_session.analysis_run_id = analysis["analysis_run_id"]
            ride_session.updated_at = now
            return "duplicate"
        raise ValidationError("analysis_run_id conflicts with an existing result", code="PI_ANALYSIS_CONFLICT", status_code=409)

    summary = _analysis_summary_payload(analysis)
    result = AnalysisResult(
        ride_session_id=ride_session.id,
        session_id=ride_session.session_id,
        analysis_run_id=analysis["analysis_run_id"],
        model_version=analysis["model_version"],
        feature_schema_version=analysis["feature_schema_version"],
        telemetry_schema_version=analysis["telemetry_schema_version"],
        ecu_profile_id=analysis["ecu_profile_id"],
        decoder_id=analysis["decoder_id"],
        decoder_version=analysis["decoder_version"],
        signal_columns=list(analysis["signal_columns"]),
        overall_status=analysis["overall_status"],
        health_score=analysis["health_score"],
        anomaly_ratio=analysis["anomaly_ratio"],
        anomaly_window_count=analysis["anomaly_window_count"],
        result_summary=summary,
        result_fingerprint=analysis["result_fingerprint"],
        analyzed_at=analysis["analyzed_at"] or now,
    )
    db.session.add(result)
    ride_session.analysis_status = "completed"
    ride_session.analysis_run_id = analysis["analysis_run_id"]
    ride_session.updated_at = now
    return "imported"


def store_pi_sync(device: Device, upload: dict) -> dict:
    records = upload["records"]
    unique_records = _unique_pi_records(records)
    now = utc_now()
    decoder_version = _ensure_pi_decoder_version()

    session_values = {
        "device_id": device.id,
        "session_id": upload["session_id"],
        "started_at": _initial_started_at(upload, now),
        "last_record_at": now,
        "ended_at": upload["capture_ended_at"] if upload["session_ended"] else None,
        "telemetry_schema_version": PI_TELEMETRY_SCHEMA_VERSION,
        "source_type": PI_SOURCE_TYPE,
        "raw_representation": PI_RAW_REPRESENTATION,
        "transport_profile_id": PI_RAW_REPRESENTATION,
        "ecu_profile_id": PI_ECU_PROFILE_ID,
        "decoder_id": PI_DECODER_ID,
        "decoder_version": PI_DECODER_VERSION,
        "capture_started_at": upload["capture_started_at"],
        "capture_ended_at": upload["capture_ended_at"],
        "duration_ms": None,
        "sample_interval_ms": upload.get("sample_interval_ms"),
        "sampling_rate_hz": upload.get("sampling_rate_hz"),
        "decoder_version_id": decoder_version.id,
        "record_count": 0,
        "sync_status": "syncing",
        "capture_status": upload["capture_status"] or "capturing",
        "termination_reason": upload["termination_reason"],
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
    if ride_session.telemetry_schema_version and ride_session.telemetry_schema_version != PI_TELEMETRY_SCHEMA_VERSION:
        raise ValidationError("session_id already exists with a different telemetry schema", code="PI_SESSION_CONFLICT", status_code=409)
    if ride_session.source_type and ride_session.source_type != PI_SOURCE_TYPE:
        raise ValidationError("session_id already exists with a different source_type", code="PI_SESSION_CONFLICT", status_code=409)
    if (ride_session.telemetry_schema_version is None or ride_session.source_type is None) and TelemetryRecord.query.filter_by(
        device_id=device.id,
        session_id=upload["session_id"],
    ).count():
        raise ValidationError("session_id already exists with legacy telemetry records", code="PI_SESSION_CONFLICT", status_code=409)
    _reconcile_pi_capture_metadata(ride_session, upload)
    ride_session.telemetry_schema_version = PI_TELEMETRY_SCHEMA_VERSION
    ride_session.source_type = PI_SOURCE_TYPE
    ride_session.raw_representation = PI_RAW_REPRESENTATION
    ride_session.transport_profile_id = PI_RAW_REPRESENTATION
    ride_session.ecu_profile_id = PI_ECU_PROFILE_ID
    ride_session.decoder_id = PI_DECODER_ID
    ride_session.decoder_version = PI_DECODER_VERSION
    if ride_session.decoder_version_id is None:
        ride_session.decoder_version_id = decoder_version.id
    if ride_session.sample_interval_ms is None and upload.get("sample_interval_ms") is not None:
        ride_session.sample_interval_ms = upload["sample_interval_ms"]
    if ride_session.sampling_rate_hz is None and upload.get("sampling_rate_hz") is not None:
        ride_session.sampling_rate_hz = upload["sampling_rate_hz"]

    incoming_by_seq = {record["seq"]: record for record in unique_records}
    existing_records = (
        TelemetryRecord.query.filter(
            TelemetryRecord.device_id == device.id,
            TelemetryRecord.session_id == upload["session_id"],
            TelemetryRecord.seq.in_(incoming_by_seq.keys()),
        )
        .all()
        if incoming_by_seq
        else []
    )
    existing_seqs = set()
    for existing in existing_records:
        expected = existing.record_fingerprint or _model_record_fingerprint(existing)
        incoming = incoming_by_seq[int(existing.seq)]
        if expected != incoming["record_fingerprint"]:
            raise ValidationError(
                f"conflicting telemetry record for seq {existing.seq}",
                code="PI_RECORD_CONFLICT",
                status_code=409,
            )
        existing_seqs.add(int(existing.seq))

    values = []
    for record in unique_records:
        if record["seq"] in existing_seqs:
            continue
        quality_flags = {
            **(record.get("quality_flags") or {}),
            "telemetry_schema_version": PI_TELEMETRY_SCHEMA_VERSION,
            "source_type": PI_SOURCE_TYPE,
            "raw_representation": PI_RAW_REPRESENTATION,
            "ecu_profile_id": PI_ECU_PROFILE_ID,
            "decoder_id": PI_DECODER_ID,
            "decoder_version": PI_DECODER_VERSION,
        }
        values.append({
            "device_id": device.id,
            "ride_session_id": ride_session.id,
            "session_id": upload["session_id"],
            "seq": record["seq"],
            "timestamp": None,
            "device_time_ms": int(round(record["timestamp_ms"])),
            "timestamp_ms": record["timestamp_ms"],
            "server_received_at": now,
            "raw_frame": record["raw_hex"],
            "raw_hex": record["raw_hex"],
            "raw_length": record["raw_length"],
            "raw_representation": PI_RAW_REPRESENTATION,
            "rpm": record["rpm"],
            "tps_voltage": record["tps_voltage"],
            "tps_raw": record["tps_raw"],
            "battery_voltage": record["battery_voltage"],
            "iat_c": record["iat_c"],
            "ect_c": record["ect_c"],
            "candidate_signals": record["candidate_signals"],
            "quality_flags": quality_flags,
            "frame_valid": record["frame_valid"],
            "checksum_valid": record["checksum_valid"],
            "decoder_valid": bool(record["frame_valid"] and record["checksum_valid"]),
            "record_fingerprint": record["record_fingerprint"],
            "created_at": now,
        })
    if values:
        db.session.execute(TelemetryRecord.__table__.insert().values(values))
    inserted_count = len(values)
    duplicate_count = len(records) - inserted_count

    ack = _sequence_ack(device, upload["session_id"])
    _refresh_pi_session_metadata(ride_session, upload=upload, contiguous_until=ack["contiguous_until"], now=now)
    analysis_state = _store_pi_analysis(ride_session, upload.get("analysis"), now)

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
    device.last_seen_at = now
    db.session.commit()
    return {
        "ok": True,
        "device_id": device.device_id,
        "session_id": upload["session_id"],
        "received_count": len(records),
        "inserted_count": inserted_count,
        "duplicate_count": duplicate_count,
        "accepted_sequences": ack,
        "capture_status": ride_session.capture_status,
        "termination_reason": ride_session.termination_reason,
        "analysis": {
            "state": analysis_state,
            "analysis_run_id": upload["analysis"]["analysis_run_id"] if upload.get("analysis") else None,
        },
        "server_time": utc_now().isoformat().replace("+00:00", "Z"),
    }
