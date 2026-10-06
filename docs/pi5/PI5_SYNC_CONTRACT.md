# Pi5 Cloud Sync Contract

## Authority Statement

Cloud owns no ECU semantic decoding.
Analyzer is the sole ECU decode authority.
RAW-only durable sessions are sent to Analyzer.
Already-decoded/analyzed Pi sessions are persisted directly.

## Endpoint

```text
POST /api/device/sync/session
Content-Type: application/json
X-Device-ID: <paired Cloud device id>
Authorization: Bearer <device token>
```

The endpoint reuses the existing `/api/device/pair` device onboarding flow.
Cloud stores only the device token hash.

## Batch Limits

`records` must be a non-empty array and may contain at most the configured
Cloud `MAX_LOG_RECORDS` value. Current test configuration uses 10; production
uses the app config value.

## Request Envelope

Required top-level provenance:

```json
{
  "telemetry_schema_version": "canonical-telemetry-v2",
  "ecu_profile_id": "honda_keihin_71_17",
  "decoder_id": "honda_keihin_71_17",
  "decoder_version": "1.0.0"
}
```

Required session fields:

- `session_id`: stable Pi session id, scoped by authenticated device.
- `records`: one or more canonical V2 records.

Optional session fields:

- `batch_id`: client batch id for diagnostics; not an idempotency key.
- `session_ended`: boolean, defaults to `false`.
- `sampling.sample_interval_ms`
- `sampling.sampling_rate_hz`
- `capture_started_at`: ISO 8601 with timezone or Unix epoch seconds.
- `capture_ended_at`: ISO 8601 with timezone or Unix epoch seconds.
- `capture_status` and `termination_reason`: optional termination pair. Omit
  both for legacy-compatible uploads. Accepted pairs are `completed` with
  `normal_stop`, or `interrupted` with `unclean_runtime_shutdown`.
- `analysis`: Pi-generated Analyzer summary, allowed only on final
  `session_ended` sync.

## Capture Termination

`session_ended` means this request carries the final available upload batch. It
does not claim how local acquisition ended. Capture termination is independently
reported with exactly one of these pairs:

```json
{"capture_status":"completed","termination_reason":"normal_stop"}
```

```json
{"capture_status":"interrupted","termination_reason":"unclean_runtime_shutdown"}
```

The same pair may be sent on every batch or only once it is known. Cloud stores
the first reported pair for the authenticated device/session and accepts exact
retries. A different pair for that identity returns HTTP 409
`PI_CAPTURE_METADATA_CONFLICT`; it never overwrites stored history. Omitted
metadata remains unknown/legacy, not an inferred normal shutdown.

`unclean_runtime_shutdown` reports that the preceding runtime did not end
cleanly. It must not be presented as a positively detected electrical power
loss or ignition-OFF event.

## Record Schema

Each record requires:

```json
{
  "seq": 0,
  "timestamp_ms": 0,
  "raw_hex": "02187117...",
  "raw_length": 24,
  "rpm": 1250,
  "tps_voltage": 0.72,
  "tps_raw": 37,
  "battery_voltage": 13.6,
  "iat_c": 31.5,
  "ect_c": 74,
  "frame_valid": true,
  "checksum_valid": true
}
```

Rules:

- `seq` is a non-negative integer.
- `timestamp_ms` is session-relative elapsed milliseconds, not wall clock.
- `raw_hex` is the native 24-byte Honda Table `0x17` frame as compact hex.
- `raw_length` must be `24`.
- Cloud persists `raw_representation = native24_table17` and does not prepend
  ESP compatibility bytes.
- Numeric values must be finite JSON numbers.
- `candidate_signals` is optional JSON object metadata.
- `quality_flags` is optional JSON object metadata.
- `map_raw` is not required for V2 and should not be fabricated.
- `tps_raw` and `ect_c` remain native V2 fields in Cloud storage.

## Analysis Schema

`analysis` is imported as an `AnalysisResult`. Required fields:

```json
{
  "analysis_run_id": "pi-run-20260920-0001",
  "model_version": "iforest-baseline-20260920T091709Z",
  "telemetry_schema_version": "canonical-telemetry-v2",
  "feature_schema_version": "ecu-window-features-v1",
  "signal_columns": ["rpm", "tps_voltage", "tps_raw", "battery_voltage", "iat_c", "ect_c"],
  "window_count": 3,
  "anomaly_window_count": 0,
  "anomaly_ratio": 0,
  "health_score": 98.5,
  "overall_status": "limited_data",
  "most_unusual_features": [],
  "model_loaded": true,
  "evidence_window_count": 3,
  "minimum_windows_for_status": 10,
  "evidence_sufficient": false,
  "warnings": [],
  "note": "Pi local analysis completed."
}
```

The current model version is
`iforest-baseline-20260920T091709Z`, but Cloud accepts future non-empty model
versions. Analysis provenance must match the session provenance. If optional
`ecu_profile_id`, `decoder_id`, or `decoder_version` are present inside
`analysis`, they must match the top-level session values.

Supported Analyzer status strings are stored unchanged:

- `ok`
- `monitor`
- `attention`
- `limited_data`
- `no_windows`
- `model_unavailable`
- `not_scored`

`limited_data` means Analyzer scoring ran but `window_count` was below the
minimum evidence gate, currently 10 windows. Pi should still send the raw
metrics (`anomaly_ratio`, `anomaly_window_count`, `health_score`, and
`window_count`) plus the optional evidence metadata when available. Cloud stores
those fields in `result_summary` and displays the result as neutral "Limited
data".

## Valid First Batch Example

```json
{
  "session_id": "pi5-ride-000123",
  "batch_id": "pi5-ride-000123-0000",
  "session_ended": false,
  "telemetry_schema_version": "canonical-telemetry-v2",
  "ecu_profile_id": "honda_keihin_71_17",
  "decoder_id": "honda_keihin_71_17",
  "decoder_version": "1.0.0",
  "sampling": {
    "sample_interval_ms": 250,
    "sampling_rate_hz": 4
  },
  "capture_started_at": "2026-09-20T09:00:00Z",
  "records": [
    {
      "seq": 0,
      "timestamp_ms": 0,
      "raw_hex": "0218711705DC1A02FFFF904D506A7F0064000000000000E9",
      "raw_length": 24,
      "rpm": 1250,
      "tps_voltage": 0.72,
      "tps_raw": 37,
      "battery_voltage": 13.6,
      "iat_c": 31.5,
      "ect_c": 74,
      "frame_valid": true,
      "checksum_valid": true,
      "candidate_signals": {}
    },
    {
      "seq": 1,
      "timestamp_ms": 250,
      "raw_hex": "0218711706011D03FFFF914E516B800064000000000000BA",
      "raw_length": 24,
      "rpm": 1280,
      "tps_voltage": 0.73,
      "tps_raw": 38,
      "battery_voltage": 13.6,
      "iat_c": 31.5,
      "ect_c": 74.5,
      "frame_valid": true,
      "checksum_valid": true,
      "candidate_signals": {}
    }
  ]
}
```

## Valid Final Batch With Analysis Example

```json
{
  "session_id": "pi5-ride-000123",
  "batch_id": "pi5-ride-000123-final",
  "session_ended": true,
  "telemetry_schema_version": "canonical-telemetry-v2",
  "ecu_profile_id": "honda_keihin_71_17",
  "decoder_id": "honda_keihin_71_17",
  "decoder_version": "1.0.0",
  "capture_ended_at": "2026-09-20T09:12:05Z",
  "capture_status": "completed",
  "termination_reason": "normal_stop",
  "records": [
    {
      "seq": 2,
      "timestamp_ms": 500,
      "raw_hex": "0218711706262004FFFF924C526C8100640000000000008F",
      "raw_length": 24,
      "rpm": 1320,
      "tps_voltage": 0.75,
      "tps_raw": 39,
      "battery_voltage": 13.7,
      "iat_c": 31.8,
      "ect_c": 75.2,
      "frame_valid": true,
      "checksum_valid": true
    }
  ],
  "analysis": {
    "analysis_run_id": "pi-run-20260920-0001",
    "model_version": "iforest-baseline-20260920T091709Z",
    "telemetry_schema_version": "canonical-telemetry-v2",
    "feature_schema_version": "ecu-window-features-v1",
    "signal_columns": ["rpm", "tps_voltage", "tps_raw", "battery_voltage", "iat_c", "ect_c"],
    "window_count": 3,
    "anomaly_window_count": 0,
    "anomaly_ratio": 0,
    "health_score": 98.5,
    "overall_status": "limited_data",
    "most_unusual_features": [],
    "model_loaded": true,
    "evidence_window_count": 3,
    "minimum_windows_for_status": 10,
    "evidence_sufficient": false,
    "warnings": [],
    "note": "Pi local analysis completed."
  }
}
```

For a recovered interrupted RideSession, retain the same stable `session_id`,
RAW records, and analysis identity, while reporting:

```json
{
  "session_ended": true,
  "capture_status": "interrupted",
  "termination_reason": "unclean_runtime_shutdown"
}
```

An interrupted session may still include valid `analysis`, including
`limited_data`; interruption alone is not an ECU anomaly or vehicle fault.

## ACK Shape

Successful sync returns HTTP 200:

```json
{
  "ok": true,
  "device_id": "pi5-native",
  "session_id": "pi5-ride-000123",
  "received_count": 1,
  "inserted_count": 1,
  "duplicate_count": 0,
  "accepted_sequences": {
    "minimum": 0,
    "maximum": 2,
    "contiguous_until": 2
  },
  "capture_status": "completed",
  "termination_reason": "normal_stop",
  "analysis": {
    "state": "imported",
    "analysis_run_id": "pi-run-20260920-0001"
  },
  "server_time": "2026-09-20T09:12:06Z"
}
```

`analysis.state` is `imported`, `duplicate`, or `null`.

## Idempotency And 409 Conflicts

Record identity is `(authenticated device, session_id, seq)`.

- Same identity plus same normalized RAW and semantic content succeeds as duplicate.
- Same identity plus different RAW or different normalized semantic content returns HTTP 409
  with `PI_RECORD_CONFLICT`.
- The record fingerprint covers `seq`, `timestamp_ms`, `raw_hex`, `raw_length`,
  V2 signal values, `frame_valid`, `checksum_valid`, and `candidate_signals`.
- Server timestamps, database ids, batch ids, and client-provided hashes are
  not trusted as record identity.

Analysis identity is `analysis_run_id`.

- Same `analysis_run_id` plus same normalized semantic result succeeds as
  duplicate.
- Same `analysis_run_id` plus different normalized semantic result returns
  HTTP 409 with `PI_ANALYSIS_CONFLICT`.
- An `analysis_run_id` already attached to another session returns 409.

Capture termination identity is the reported pair for the authenticated device
and `session_id`.

- A matching pair is idempotent across batches and retries.
- A different pair returns HTTP 409 with `PI_CAPTURE_METADATA_CONFLICT`.

## Finalization And Time

On `session_ended: true`, Cloud recomputes upload state from stored records:

- Contiguous sequence coverage produces `sync_status=complete`.
- Gaps produce `sync_status=partial`.
- When explicit termination metadata is present, `capture_status` remains the
  reported `completed` or `interrupted` value regardless of upload state.
- Without explicit termination metadata, legacy derived `capture_status`
  behavior remains for compatibility, while `termination_reason` stays null.
- `duration_ms` is `max(timestamp_ms) - min(timestamp_ms)` from telemetry.
- Cloud does not infer ride duration from upload receive span.
- `capture_started_at` and `capture_ended_at` remain optional wall-clock
  metadata.
- `server_received_at` remains Cloud ingest/audit time only.

## Retry Guidance

Pi should retry with the same `session_id`, `seq`, and semantic record content.
It can split a session across multiple batches, upload out of order, and retry
the final batch. Cloud ACKs always report the stored sequence range for the
authenticated device/session.

## ESP Boundary

This endpoint is additive. Existing ESP endpoints remain unchanged:

- `/api/device/pair`
- `/api/logs/upload`
- `/api/telemetry/live`
- `/api/device/control/poll`
- `/api/device/control/ack`
