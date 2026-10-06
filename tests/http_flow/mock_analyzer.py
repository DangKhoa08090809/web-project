from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from flask import Flask, jsonify, request

from tools.http_sim.frame_factory import decode_native_v2
from tools.http_sim.trace import HttpTrace


@dataclass(slots=True)
class MockAnalyzerState:
    call_count: int = 0
    requests: list[dict[str, Any]] = field(default_factory=list)
    responses: list[dict[str, Any]] = field(default_factory=list)


def create_mock_analyzer_app(
    state: MockAnalyzerState | None = None,
    *,
    trace: HttpTrace | None = None,
    capture_requests: bool = True,
    score_payload: bool = False,
) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.extensions["mock_analyzer_state"] = state or MockAnalyzerState()
    app.extensions["mock_analyzer_trace"] = trace

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "mock": True})

    @app.post("/api/v1/analysis")
    def analyze():
        return jsonify({"error": "canonical analysis endpoint is not used by ESP RAW flow"}), 410

    @app.post("/api/v1/raw/decode-analyze")
    def decode_analyze_raw():
        analyzer_state: MockAnalyzerState = app.extensions["mock_analyzer_state"]
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "JSON object required"}), 400
        try:
            canonical_session = _canonical_from_raw_payload(payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 422

        analyzer_state.call_count += 1
        if capture_requests:
            analyzer_state.requests.append(payload)
        else:
            analyzer_state.requests.append({
                "session_id": payload.get("session_id"),
                "sample_count": len(payload.get("records", [])),
                "raw_representation": payload.get("raw_representation"),
            })
        session_id = str(payload.get("session_id") or "session")[:70]
        scored = _score_payload(canonical_session) if score_payload else {}
        analysis_payload = {
            "analysis_run_id": f"mock-cloud-analysis-{session_id}-{analyzer_state.call_count:03d}",
            "session_id": payload.get("session_id"),
            "model_version": "mock-v1-model",
            "telemetry_schema_version": "canonical-telemetry-v2",
            "feature_schema_version": "ecu-window-features-v1",
            "signal_columns": ["rpm", "tps_voltage", "tps_raw", "battery_voltage", "iat_c", "ect_c"],
            "ecu_profile_id": "honda_keihin_71_17",
            "decoder_id": "honda_keihin_71_17",
            "decoder_version": "1.0.0",
            "overall_status": "ok",
            "health_score": 90.0,
            "anomaly_ratio": 0.0,
            "anomaly_window_count": 0,
            "window_count": max(1, len(canonical_session.get("samples", [])) // 30),
            "note": "Mock Analyzer HTTP fixture",
        }
        analysis_payload.update(scored)
        response_payload = {"canonical_session": canonical_session, "analysis": analysis_payload}
        analyzer_state.responses.append(response_payload)

        current_trace: HttpTrace | None = app.extensions.get("mock_analyzer_trace")
        if current_trace is not None:
            suffix = "" if analyzer_state.call_count == 1 else f"_{analyzer_state.call_count:04d}"
            current_trace.log_request(
                label="CLOUD -> ANALYZER",
                method="POST",
                url="/api/v1/raw/decode-analyze",
                headers=dict(request.headers),
                body=payload,
                artifact_name=f"cloud_to_analyzer_request{suffix}.json",
            )
            current_trace.log_response(
                label="ANALYZER -> CLOUD",
                status_code=200,
                body=response_payload,
                artifact_name=f"analyzer_to_cloud_response{suffix}.json",
            )

        return jsonify(response_payload)

    return app


def _native_frame_from_raw(record: dict[str, Any], raw_representation: str) -> bytes:
    raw_hex = record.get("raw_hex")
    if not isinstance(raw_hex, str) or not raw_hex.strip():
        raise ValueError("raw_hex is required")
    frame = bytes.fromhex(raw_hex.strip())
    if raw_representation == "legacy29_ff5":
        if len(frame) != 29 or frame[:5] != bytes([0xFF] * 5):
            raise ValueError("legacy29_ff5 records must contain FFx5 + native24")
        return frame[5:]
    if raw_representation == "native24_table17":
        return frame
    raise ValueError(f"unsupported raw_representation {raw_representation!r}")


def _canonical_from_raw_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw_representation = payload.get("raw_representation")
    records = payload.get("records")
    if raw_representation not in {"legacy29_ff5", "native24_table17"}:
        raise ValueError("raw_representation must be legacy29_ff5 or native24_table17")
    if not isinstance(records, list) or not records:
        raise ValueError("records must be a non-empty array")

    samples = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError("record must be an object")
        decoded = decode_native_v2(_native_frame_from_raw(record, raw_representation))
        sequence = record.get("sequence", index)
        sample = {
            "sequence": int(sequence),
            "timestamp_ms": record.get("timestamp_ms"),
            **decoded,
        }
        samples.append(sample)
    return {
        "session_id": payload.get("session_id"),
        "vehicle_id": payload.get("vehicle_id"),
        "device_id": payload.get("device_id"),
        "ecu_profile_id": "honda_keihin_71_17",
        "decoder_id": "honda_keihin_71_17",
        "decoder_version": "1.0.0",
        "telemetry_schema_version": "canonical-telemetry-v2",
        "sampling": payload.get("sampling") or {},
        "samples": samples,
        "signal_definitions": {
            "rpm": {"status": "verified", "unit": "rpm"},
            "tps_voltage": {"status": "verified", "unit": "V"},
            "tps_raw": {"status": "verified_as_raw", "unit": "raw"},
            "battery_voltage": {"status": "verified", "unit": "V"},
            "iat_c": {"status": "provisional_high_confidence", "unit": "degC"},
            "ect_c": {"status": "provisional_high_confidence", "unit": "degC"},
        },
        "source_type": "raw_compatibility_adapter",
    }


def _score_payload(payload: dict[str, Any]) -> dict[str, Any]:
    samples = payload.get("samples", [])
    if not isinstance(samples, list) or not samples:
        return {}
    bad = 0
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        battery = sample.get("battery_voltage")
        ect = sample.get("ect_c_candidate", sample.get("ect_c"))
        rpm = sample.get("rpm")
        if isinstance(battery, (int, float)) and battery < 11.8:
            bad += 1
        elif isinstance(ect, (int, float)) and ect >= 112:
            bad += 1
        elif isinstance(rpm, (int, float)) and rpm >= 5000:
            bad += 1
    ratio = bad / len(samples)
    if ratio <= 0:
        return {}
    return {
        "overall_status": "requires_attention" if ratio < 0.2 else "high_anomaly",
        "health_score": max(5.0, round(90.0 - ratio * 260, 1)),
        "anomaly_ratio": round(ratio, 4),
        "anomaly_window_count": max(1, int(ratio * max(1, len(samples) // 30))),
        "note": "Mock Analyzer detected seeded HTTP demo anomalies.",
    }
