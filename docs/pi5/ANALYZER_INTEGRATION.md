# Analyzer Integration

## Authority Statement

Cloud owns no ECU semantic decoding.
Analyzer is the sole ECU decode authority.
RAW-only durable sessions are sent to Analyzer.
Already-decoded/analyzed Pi sessions are persisted directly.

## Mechanism

Cloud integrates with Analyzer over HTTP.

- Client code: `integrations/analyzer_client.py`.
- Base URL: `ML_API_BASE_URL`.
- Timeout: `ML_API_TIMEOUT_SECONDS`.
- Health check: `GET <base_url>/health`.
- ESP RAW analysis call: `POST <base_url>/api/v1/raw/decode-analyze`.
- Payload: stored RAW records, not Cloud-decoded canonical telemetry.

Cloud does not:

- import Analyzer Python modules;
- create temporary RAW JSONL files;
- semantically decode ECU bytes locally;
- run subprocesses/scripts;
- require Analyzer to be on the same physical server.

It only requires the configured HTTP URL to be reachable from the Cloud process.

## Trigger Points

Automatic trigger:

- `services.telemetry.store_upload` commits a completed upload.
- If `session_ended == true` and `ML_API_BASE_URL` is non-empty, it calls
  `submit_session_to_analyzer(ride_session.id)`.

Manual trigger:

- Browser route `POST /api/sessions/<session_pk>/analysis/re-run` calls
  `submit_session_to_analyzer(..., force=True)`.

There is no background queue. Upload request latency can include analyzer call
time on final batches.

## Input Built For Analyzer

`services.analysis_submission` loads stored `TelemetryRecord` rows ordered by
`seq` and builds:

```json
{
  "session_id": "...",
  "device_id": "...",
  "vehicle_id": "...",
  "raw_representation": "legacy29_ff5",
  "sampling": {"sample_interval_ms": 250},
  "process_with_model": true,
  "records": [
    {"sequence": 0, "timestamp_ms": 0, "raw_hex": "FFFFFFFFFF..."}
  ]
}
```

For ESP durable uploads, `timestamp_ms` is copied from `device_time_ms` because
it is session-relative elapsed time.

## Output Stored From Analyzer

Analyzer response JSON must include:

```text
canonical_session
analysis
```

Cloud validates that `canonical_session` is:

```text
telemetry_schema_version = canonical-telemetry-v2
decoder_id = honda_keihin_71_17
decoder_version = 1.0.0
```

Cloud then updates existing telemetry rows by `sequence`, preserving RAW fields
and writing V2 fields: `rpm`, `tps_voltage`, `tps_raw`, `battery_voltage`,
`iat_c`, `ect_c`, validity flags, candidate signals, quality flags, and
`timestamp_ms`.

Stored in `AnalysisResult`:

- `analysis_run_id`
- `model_version`
- `telemetry_schema_version`
- `ecu_profile_id`
- `decoder_id`
- `decoder_version`
- `feature_schema_version`
- `signal_columns`
- `overall_status`
- `health_score`
- `anomaly_ratio`
- `anomaly_window_count`
- full `result_summary` JSON
- `analyzed_at`

Cloud also sets:

- `RideSession.analysis_status = completed`
- `RideSession.analysis_run_id = <latest run id>`

Cloud does not store Analyzer windows, features, frame data, model artifacts, or
full file-backed Analyzer repository outputs.

## Failure Semantics

| Condition | Current Cloud behavior |
| --- | --- |
| Analyzer not configured | Auto final upload leaves `analysis_status = not_requested`; forced rerun sets `failed` and returns HTTP 503. |
| Analyzer unavailable/network error | `analysis_status = failed`; stored telemetry is preserved. |
| Analyzer timeout | Treated as request failure; `analysis_status = failed`. |
| Analyzer HTTP error, including 422 invalid RAW or insufficient samples | Treated as request failure; `analysis_status = failed`. |
| Invalid/non-JSON response | `analysis_status = failed`. |
| Model unavailable but Analyzer returns a valid summary | Stored as a completed analysis. |

## 29-Byte To Analyzer Bridge

Answers to the required bridge questions:

1. Cloud maps production `raw_frame`/`raw_hex` to Analyzer `raw_hex`.
2. Cloud does not create a temporary JSONL/file for Analyzer.
3. Cloud calls Analyzer HTTP `POST /api/v1/raw/decode-analyze`.
4. Cloud does not import Analyzer Python code.
5. Cloud does not use subprocess/script integration.
6. Cloud does not decode RAW itself.
7. Cloud stores the 29-byte compatibility frame as `TelemetryRecord.raw_frame`.
8. Cloud does not normalize to 24 bytes before analysis; Analyzer does.
9. Cloud treats `honda_keihin_legacy_29` as transport compatibility metadata
   and persists `raw_representation = legacy29_ff5`.
