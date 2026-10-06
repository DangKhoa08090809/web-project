from __future__ import annotations

import hashlib
import json

from flask import current_app

from extensions import db
from integrations.analyzer_client import AnalyzerClient, AnalyzerClientError
from models import AnalysisResult, RideSession, TelemetryRecord, utc_now
from services.pi_sync import PI_SOURCE_TYPE


def analyzer_client_from_config() -> AnalyzerClient:
    return AnalyzerClient(
        base_url=current_app.config.get("ML_API_BASE_URL", ""),
        timeout_seconds=float(current_app.config.get("ML_API_TIMEOUT_SECONDS", 2.5)),
    )


def _store_analysis_success(ride_session: RideSession, summary: dict) -> None:
    summary = _analysis_summary_with_safe_diagnostic_evidence(summary)
    analysis_run_id = str(summary.get("analysis_run_id") or "")
    if not analysis_run_id:
        raise AnalyzerClientError("analysis response did not include analysis_run_id")
    analyzed_at = utc_now()
    result = AnalysisResult.query.filter_by(analysis_run_id=analysis_run_id).first()
    if result is None:
        result = AnalysisResult(
            ride_session_id=ride_session.id,
            session_id=ride_session.session_id,
            analysis_run_id=analysis_run_id,
        )
        db.session.add(result)
    signal_columns = summary.get("signal_columns")
    result.model_version = summary.get("model_version")
    result.feature_schema_version = summary.get("feature_schema_version")
    result.telemetry_schema_version = summary.get("telemetry_schema_version")
    result.ecu_profile_id = summary.get("ecu_profile_id")
    result.decoder_id = summary.get("decoder_id")
    result.decoder_version = summary.get("decoder_version")
    result.signal_columns = signal_columns if isinstance(signal_columns, list) else None
    result.overall_status = summary.get("overall_status")
    result.health_score = summary.get("health_score")
    result.anomaly_ratio = summary.get("anomaly_ratio")
    result.anomaly_window_count = summary.get("anomaly_window_count")
    result.result_summary = summary
    result.result_fingerprint = _json_fingerprint(summary)
    result.analyzed_at = analyzed_at
    ride_session.analysis_status = "completed"
    ride_session.analysis_run_id = analysis_run_id
    ride_session.updated_at = analyzed_at


def _json_fingerprint(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _analysis_summary_with_safe_diagnostic_evidence(summary: dict) -> dict:
    sanitized = dict(summary)
    if "diagnostic_evidence" not in sanitized or sanitized.get("diagnostic_evidence") is None:
        return sanitized

    evidence = sanitized.get("diagnostic_evidence")
    if isinstance(evidence, dict):
        try:
            encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            sanitized["diagnostic_evidence"] = json.loads(encoded)
            return sanitized
        except (TypeError, ValueError):
            pass

    warnings = sanitized.get("warnings")
    if isinstance(warnings, list):
        warnings = [str(item)[:255] for item in warnings]
    elif warnings:
        warnings = [str(warnings)[:255]]
    else:
        warnings = []
    warnings.append("diagnostic_evidence ignored because it was not a JSON object.")
    sanitized["warnings"] = warnings
    sanitized.pop("diagnostic_evidence", None)
    return sanitized


def _latest_analysis(ride_session: RideSession):
    return (
        AnalysisResult.query.filter_by(ride_session_id=ride_session.id)
        .order_by(AnalysisResult.analyzed_at.desc())
        .first()
    )


def _record_raw_hex(record: TelemetryRecord) -> str | None:
    if record.raw_hex:
        return str(record.raw_hex).strip().upper()
    raw_frame = record.raw_frame
    if isinstance(raw_frame, str):
        return raw_frame.replace(";", "").strip().upper()
    if isinstance(raw_frame, dict):
        for key in ("hex", "raw_hex", "frame_hex"):
            value = raw_frame.get(key)
            if isinstance(value, str) and value.strip():
                return value.replace(";", "").replace(" ", "").strip().upper()
    return None


def _raw_records_for_analyzer(ride_session: RideSession) -> list[dict]:
    records = (
        TelemetryRecord.query.filter_by(ride_session_id=ride_session.id)
        .order_by(TelemetryRecord.seq)
        .all()
    )
    payload_records = []
    for record in records:
        raw_hex = _record_raw_hex(record)
        if not raw_hex:
            continue
        timestamp_ms = record.device_time_ms if record.device_time_ms is not None else record.timestamp_ms
        payload_records.append({
            "sequence": int(record.seq),
            "timestamp_ms": float(timestamp_ms) if timestamp_ms is not None else None,
            "raw_hex": raw_hex,
        })
    return payload_records


def _raw_decode_request(ride_session: RideSession) -> dict:
    records = _raw_records_for_analyzer(ride_session)
    if not records:
        raise AnalyzerClientError("Stored RAW is required for Analyzer RAW reprocess.")
    device = ride_session.device
    sampling = {}
    if ride_session.sample_interval_ms is not None:
        sampling["sample_interval_ms"] = float(ride_session.sample_interval_ms)
    if ride_session.sampling_rate_hz is not None:
        sampling["sampling_rate_hz"] = float(ride_session.sampling_rate_hz)
    return {
        "session_id": ride_session.session_id,
        "device_id": device.device_id,
        "vehicle_id": device.vehicle_name,
        "raw_representation": ride_session.raw_representation or "legacy29_ff5",
        "sampling": sampling,
        "records": records,
        "process_with_model": True,
    }


def _validate_raw_response(payload: dict) -> tuple[dict, dict]:
    if not isinstance(payload, dict):
        raise AnalyzerClientError("analysis response was not a JSON object")
    canonical = payload.get("canonical_session")
    analysis = payload.get("analysis")
    if not isinstance(canonical, dict) or not isinstance(analysis, dict):
        raise AnalyzerClientError("analysis response must include canonical_session and analysis objects")
    expected = {
        "telemetry_schema_version": "canonical-telemetry-v2",
        "decoder_id": "honda_keihin_71_17",
        "decoder_version": "1.0.0",
    }
    for key, value in expected.items():
        if canonical.get(key) != value:
            raise AnalyzerClientError(f"canonical_session.{key} must be {value}")
    return canonical, analysis


def _apply_canonical_session(ride_session: RideSession, canonical: dict) -> None:
    samples = canonical.get("samples")
    if not isinstance(samples, list):
        raise AnalyzerClientError("canonical_session.samples must be an array")
    existing = {
        int(record.seq): record
        for record in TelemetryRecord.query.filter_by(ride_session_id=ride_session.id).all()
    }
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        sequence = sample.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise AnalyzerClientError("canonical sample sequence must be an integer")
        record = existing.get(sequence)
        if record is None:
            raise AnalyzerClientError(f"canonical sample sequence {sequence} does not match stored RAW")
        record.timestamp_ms = sample.get("timestamp_ms")
        record.rpm = sample.get("rpm")
        record.tps_voltage = sample.get("tps_voltage")
        record.tps_raw = sample.get("tps_raw")
        record.battery_voltage = sample.get("battery_voltage")
        record.iat_c = sample.get("iat_c")
        record.ect_c = sample.get("ect_c")
        record.tps_raw_candidate = None
        record.ect_c_candidate = None
        record.map_raw = None
        record.candidate_signals = sample.get("candidate_signals") or {}
        record.quality_flags = {
            **(sample.get("quality_flags") or {}),
            "decoded_by": "analyzer",
            "raw_representation": ride_session.raw_representation or "legacy29_ff5",
        }
        record.frame_valid = bool(sample.get("frame_valid", True))
        record.checksum_valid = bool(sample.get("checksum_valid", True))
        record.decoder_valid = bool(record.frame_valid and record.checksum_valid)

    ride_session.telemetry_schema_version = canonical["telemetry_schema_version"]
    ride_session.ecu_profile_id = canonical.get("ecu_profile_id") or "honda_keihin_71_17"
    ride_session.decoder_id = canonical["decoder_id"]
    ride_session.decoder_version = canonical["decoder_version"]
    ride_session.source_type = ride_session.source_type or "esp_raw"
    ride_session.raw_representation = ride_session.raw_representation or "legacy29_ff5"
    sampling = canonical.get("sampling") or {}
    if ride_session.sample_interval_ms is None and sampling.get("sample_interval_ms") is not None:
        ride_session.sample_interval_ms = float(sampling["sample_interval_ms"])
    if ride_session.sampling_rate_hz is None and sampling.get("sampling_rate_hz") is not None:
        ride_session.sampling_rate_hz = float(sampling["sampling_rate_hz"])
    timestamps = [
        float(sample["timestamp_ms"])
        for sample in samples
        if isinstance(sample, dict) and isinstance(sample.get("timestamp_ms"), (int, float))
    ]
    if timestamps:
        ride_session.duration_ms = max(timestamps) - min(timestamps)
    ride_session.updated_at = utc_now()


def _merge_analysis_provenance(analysis: dict, canonical: dict) -> dict:
    summary = _analysis_summary_with_safe_diagnostic_evidence(analysis)
    summary.setdefault("telemetry_schema_version", canonical.get("telemetry_schema_version"))
    summary.setdefault("ecu_profile_id", canonical.get("ecu_profile_id") or "honda_keihin_71_17")
    summary.setdefault("decoder_id", canonical.get("decoder_id"))
    summary.setdefault("decoder_version", canonical.get("decoder_version"))
    return summary


def _pi_imported_result(ride_session: RideSession) -> dict:
    latest = _latest_analysis(ride_session)
    return {
        "state": "completed" if latest else "not_requested",
        "analysis": latest.result_summary if latest else None,
        "message": "Pi-native analysis is imported from the device and is not rerun by Cloud.",
    }


def submit_session_to_analyzer(ride_session_id: int, *, force: bool = False) -> dict:
    ride_session = db.session.get(RideSession, ride_session_id)
    if ride_session is None:
        raise AnalyzerClientError(f"session {ride_session_id} was not found")
    if ride_session.source_type == PI_SOURCE_TYPE:
        return _pi_imported_result(ride_session)
    if ride_session.analysis_status == "completed" and not force:
        latest = _latest_analysis(ride_session)
        return {"state": "completed", "analysis": latest.result_summary if latest else None}
    if ride_session.source_type != "esp_raw" or ride_session.raw_representation != "legacy29_ff5":
        ride_session.analysis_status = "failed" if force else ride_session.analysis_status
        ride_session.updated_at = utc_now()
        db.session.commit()
        return {
            "state": "failed",
            "error": "RAW-backed ESP session metadata is required for Analyzer RAW reprocess.",
        }

    client = analyzer_client_from_config()
    if not client.configured:
        ride_session.analysis_status = "failed" if force else "not_requested"
        ride_session.updated_at = utc_now()
        db.session.commit()
        return {"state": ride_session.analysis_status, "error": "ML API is not configured."}

    ride_session.analysis_status = "pending"
    ride_session.updated_at = utc_now()
    db.session.commit()

    try:
        ride_session = db.session.get(RideSession, ride_session_id)
        ride_session.analysis_status = "submitted"
        ride_session.updated_at = utc_now()
        db.session.commit()
        response = client.decode_analyze_raw_session(_raw_decode_request(ride_session))
        canonical, analysis = _validate_raw_response(response)
        ride_session = db.session.get(RideSession, ride_session_id)
        _apply_canonical_session(ride_session, canonical)
        summary = _merge_analysis_provenance(analysis, canonical)
    except AnalyzerClientError as exc:
        ride_session = db.session.get(RideSession, ride_session_id)
        ride_session.analysis_status = "failed"
        ride_session.updated_at = utc_now()
        db.session.commit()
        return {"state": "failed", "error": str(exc)}

    ride_session = db.session.get(RideSession, ride_session_id)
    _store_analysis_success(ride_session, summary)
    db.session.commit()
    return {"state": "completed", "analysis": summary}
