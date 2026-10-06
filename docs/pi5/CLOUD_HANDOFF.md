# Cloud Handoff

## Authority Statement

Cloud owns no ECU semantic decoding.
Analyzer is the sole ECU decode authority.
RAW-only durable sessions are sent to Analyzer.
Already-decoded/analyzed Pi sessions are persisted directly.

## Short Answer

Cloud is an ESP-compatible Flask app with an additive Pi5 native sync path.
ESP uploads 29-byte compatibility RAW frames to `/api/logs/upload`; Cloud stores
RAW first and does not semantically decode ECU bytes. On completed RAW sessions
Cloud calls Analyzer `POST /api/v1/raw/decode-analyze`, then stores Analyzer's
canonical V2 telemetry and analysis summary.

Pi5 native sync uses the same device pairing/token auth but posts native RAW24,
decoded canonical V2 telemetry, and Pi-generated analysis to
`/api/device/sync/session`. Cloud stores and displays that result; it never
routes Pi imported sessions through Cloud Analyzer.

Cloud owns no ECU semantic decoder. Analyzer is the sole ECU decode authority.

## How A Device Authenticates

- Pairing: `POST /api/device/pair` with a user-created pairing code.
- Device auth after pairing: `X-Device-ID` and `Authorization: Bearer <token>`.
- DB stores only `Device.token_hash`.
- Inactive devices are rejected.

## How A Device Belongs To A User/Vehicle

- `Device.user_id` owns the device.
- Pairing code owner becomes the device owner.
- Current durable sessions are associated with the device.
- Cloud does not create a `Vehicle` row during pairing.
- Display/canonical `vehicle_id` currently uses `Device.vehicle_name`.

## How Sessions Are Identified

- Effective identity: authenticated device plus client `session_id`.
- `RideSession` unique constraint: `(device_id, session_id)`.
- `TelemetryRecord` unique constraint: `(device_id, session_id, seq)`.
- ESP duplicate sequence inserts are ignored as before.
- Pi sync performs content fingerprint checks: identical duplicate content is
  accepted; conflicting duplicate content returns HTTP 409.

## How ESP Currently Uploads

```text
POST /api/logs/upload
headers: X-Device-ID, Authorization: Bearer token
body: session_id, session_ended, batch_id, ecu_profile_id, records[]
record: seq, device_time_ms, raw_frame
```

Cloud validates transport fields, inserts session/records, stores a `SyncBatch`,
stores `RawArtifact` metadata, refreshes session status, commits RAW, and
returns `accepted_sequences`. If the session is final and Analyzer is
configured, Cloud calls Analyzer after the RAW commit.

## Where 29-Byte Frames Are Transformed

Cloud no longer transforms or decodes 29-byte frames. It persists the transport
representation explicitly:

```text
raw_representation = legacy29_ff5
transport_profile_id = honda_keihin_legacy_29
```

`raw_frame`/`raw_hex` are stored unchanged. Analyzer strips the five generated
`FF` bytes, decodes native24 with `honda_keihin_71_17:1.0.0`, builds
`canonical-telemetry-v2`, and returns analysis.

## How Analyzer Is Invoked

- Protocol: HTTP.
- Endpoint: `POST <ML_API_BASE_URL>/api/v1/raw/decode-analyze`.
- Input: stored RAW transport records:
  `sequence`, `timestamp_ms = device_time_ms`, `raw_hex`.
- Trigger: ESP final upload if configured, or manual browser rerun for ESP
  sessions.
- Failure: session telemetry stays stored; `analysis_status` becomes `failed`
  for configured/request failures.
- Pi V2 analysis is imported from the Pi sync payload and is not rerun by Cloud.

## What Cloud Persists

- Device/user/session/record rows.
- RAW frame JSON/string inside `TelemetryRecord.raw_frame`, plus explicit
  `raw_hex`, `raw_length`, and `raw_representation`.
- `RawArtifact` metadata pointing to DB-stored raw frames.
- Analyzer-returned semantic provenance on sessions and analysis results:
  `ecu_profile_id`, `decoder_id`, `decoder_version`.
- Sync batch ACK history.
- Analyzer summary rows in `AnalysisResult`.

Cloud does not persist full Analyzer windows/features/artifacts.
Cloud stores Analyzer `overall_status` as a plain string. There is no database
enum or migration required for new Analyzer statuses such as `limited_data`.
Optional evidence fields from the summary are retained in
`AnalysisResult.result_summary`.

## How Analysis Reaches Frontend

Frontend pages and APIs read Cloud DB through `services.analysis`.

- Charts and playback require `TelemetryRecord` rows.
- Local statistical screening runs from stored samples.
- Latest `AnalysisResult` contributes model/status/health fields and is used as
  the primary session result label when present.
- Opening a page does not rerun Analyzer.

The `limited_data` status is displayed as a neutral "Limited data" result. It
means scoring completed but there were fewer than 10 evidence windows, so the UI
does not call the session normal or anomalous. Stored anomaly metrics remain
visible/preserved for inspection.

## How Retries/Idempotency Work

- ESP: repeated `(device, session_id, seq)` rows are duplicates and do not fail.
  ACK is recomputed from all stored sequences. `batch_id` is not unique.
- Pi: repeated `(device, session_id, seq)` rows are accepted only when the
  normalized semantic record fingerprint matches. Different content returns
  `PI_RECORD_CONFLICT` with HTTP 409.
- Pi analysis import is idempotent by `analysis_run_id`; different semantic
  result for the same run id returns `PI_ANALYSIS_CONFLICT` with HTTP 409.

## Pi Sync Path

```text
POST /api/device/sync/session
  -> device token auth
  -> canonical-telemetry-v2 provenance validation
  -> content-fingerprint/idempotency check
  -> RideSession/TelemetryRecord/SyncBatch write
  -> optional AnalysisResult import
  -> ACK
```

Pi required provenance:

```text
telemetry_schema_version = canonical-telemetry-v2
ecu_profile_id = honda_keihin_71_17
decoder_id = honda_keihin_71_17
decoder_version = 1.0.0
```

Pi required record fields:

```text
seq, timestamp_ms, raw_hex, raw_length, rpm, tps_voltage, tps_raw, battery_voltage,
iat_c, ect_c, frame_valid, checksum_valid
```

See `docs/pi5/PI5_SYNC_CONTRACT.md` for the full JSON contract.

Useful later:

- Structured `device_type` / capabilities.
- Actual `Vehicle.id` association.
- Richer Analyzer artifact storage.

Not needed for MVP:

- Cloud rerunning Pi analysis.
- New OAuth/user login flow on Pi.
- Reusing Cloud Live Mode for Pi local Live View.
- Queues/brokers/microservices.

## Remaining Unknowns

- Whether Pi will later sync native 24-byte RAW artifacts in addition to decoded
  canonical V2 records.
- Long-term model artifact/version naming beyond the current
  `iforest-baseline-20260920T091709Z` baseline.
- Whether product requires actual `Vehicle` rows or current device vehicle name
  is enough for MVP.
- Whether imported Pi analysis needs per-window detail in Cloud later.
