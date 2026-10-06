# Pi5 Cloud Gap Analysis

## Target MVP

Minimum viable Pi Cloud behavior:

```text
Pi can pair to an account.
Pi can upload/sync a locally-created completed session.
Cloud associates the session with the correct user/device/vehicle label.
Cloud can display Pi-generated analysis.
Retry is idempotent.
ESP flows continue to work unchanged.
```

Implementation status: this MVP is implemented through
`POST /api/device/sync/session` with existing device pairing/token auth. The
endpoint accepts canonical V2 Pi telemetry, imports optional Pi-generated
analysis, enforces Pi content conflicts, and leaves ESP endpoints unchanged.

## Existing Capabilities That Can Be Reused

| Capability | Status |
| --- | --- |
| Device pairing | Reuse existing `/api/device/pair`. |
| Device token auth | Reuse existing `X-Device-ID` + bearer token. |
| User ownership | Already through `Device.user_id`. |
| Device metadata | Existing fields can carry Pi name, firmware, hardware, ECU type. |
| Durable session tables | Reused through additive V2/provenance/fingerprint columns. |
| Frontend session display | V1 and V2 records render from stored `RideSession` and `TelemetryRecord`. |
| Analysis display | Latest imported `AnalysisResult` is surfaced through existing analysis APIs. |

## Gaps

| Gap | Classification | Reason |
| --- | --- | --- |
| Dedicated way to sync decoded/canonical Pi telemetry | IMPLEMENTED | `/api/device/sync/session` accepts canonical V2 records. |
| Trusted device-generated analysis import | IMPLEMENTED | Final Pi sync can import an `AnalysisResult` without Cloud Analyzer submission. |
| Idempotent content hash and conflict handling | IMPLEMENTED | Pi record and analysis fingerprints accept identical retries and reject semantic conflicts with HTTP 409. |
| Decoder/model provenance from Pi | IMPLEMENTED | Session, decoder, and analysis provenance are persisted. |
| Preserve ESP `/api/logs/upload` behavior | IMPLEMENTED | ESP routes and duplicate semantics remain unchanged. |
| Explicit session-local time semantics | IMPLEMENTED | Pi `duration_ms` is derived from stored relative `timestamp_ms`, separate from upload receive time. |
| Optional RAW artifact sync with representation metadata | USEFUL LATER | Existing UI can work from decoded telemetry, but optional RAW improves audit/replay. |
| Device `device_type` / structured capabilities | USEFUL LATER | Existing device fields can represent Pi, but structured capabilities would make feature gating clearer. |
| Binding sessions to actual `Vehicle.id` | USEFUL LATER | Current product displays `Device.vehicle_name`; `RideSession.vehicle_id` exists but upload does not set it. |
| Cloud rerunning Pi analysis | NOT NEEDED FOR MVP | Pi is source of truth for local analysis; Cloud should store/display Pi result. |
| Cloud Live Mode reuse for Pi local Live View | NOT NEEDED FOR MVP | Pi local Live View is offline/local and outside Cloud critical path. |
| New OAuth/user login flow on Pi | NOT NEEDED FOR MVP | Device pairing/token model is enough for Pi-to-Cloud sync. |

## Required Minimal Change Shape

Implemented path:

```text
Pi sync request
  -> /api/device/sync/session
  -> device token auth
  -> canonical V2 provenance validation
  -> content-fingerprint/idempotency check
  -> RideSession upsert
  -> TelemetryRecord insert or 409 conflict
  -> AnalysisResult import or 409 conflict
  -> ACK
```

Implemented payload concepts:

- `session_id`
- `session_ended`
- session start/end metadata, clearly separating wall-clock and elapsed time
- decoded canonical V2 records shaped for native `TelemetryRecord` columns
- `telemetry_schema_version`
- `ecu_profile_id`
- `decoder_id`
- `decoder_version`
- `feature_schema_version`
- `model_version`
- `analysis_run_id`
- `analysis`
- server-computed record and analysis fingerprints

Native RAW artifact sync remains out of scope for this MVP.

## Do Not Change For MVP

- Do not redesign Analyzer.
- Do not change ESP payloads.
- Do not move Pi critical runtime into Cloud.
- Do not introduce queues/brokers unless a later scalability task requires it.
- Do not require Cloud Analyzer to rerun Pi sessions.
