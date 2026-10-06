# Pi Cloud Sync Contract

Authoritative handoff for Raspberry Pi EcuRead client staging integration.
Audited in the DriveSafe Cloud/Web repository on 2026-10-02.

## Source Audit

Implementation:

- Route registration: `app.py:49-53` registers `ingest_bp`; `routes/ingest.py:15` sets `url_prefix="/api"`.
- Endpoint: `routes/ingest.py:156-173`, `POST /api/device/sync/session`, handler `device_sync_session`.
- Auth wrapper: `routes/ingest.py:22-39`.
- Error envelope: `routes/ingest.py:18-19`.
- Service validation/storage: `services/pi_sync.py:16-690`.
- Persistence models: `models.py:43-75`, `models.py:226-418`.
- Pairing/provisioning: `routes/ingest.py:57-79`, `services/provisioning.py:39-148`, `routes/dashboard.py:215-227`, `routes/dashboard.py:493-509`.
- Readback/display: `routes/dashboard.py:283-294`, `routes/dashboard.py:334-357`, `services/analysis.py:12-28`, `services/analysis.py:348-366`, `services/analysis.py:725-756`.
- Pi Analyzer short-circuit: `services/analysis_submission.py:207-221`.

## Endpoint

```text
POST /api/device/sync/session
Content-Type: application/json
X-Device-ID: <paired Cloud device id>
Authorization: Bearer <device token>
```

Actual route is exactly the expected route. There is no compatibility alias.

The route is on the `ingest` blueprint with `/api` prefix. The handler requires JSON, validates with `validate_pi_sync`, persists with `store_pi_sync`, and returns HTTP 200 for accepted new, repeated, or mixed batches.

## Authentication

Required headers:

- `X-Device-ID`: Cloud device id string.
- `Authorization`: exactly a Bearer auth header with a non-empty token after the scheme.

Token behavior:

- Cloud looks up `Device.device_id`.
- Cloud stores only `Device.token_hash`.
- Verification is `sha256(token)` plus constant-time `hmac.compare_digest`.
- Rotating a token replaces the hash; old tokens behave as wrong tokens.
- Inactive devices return 403.

Error bodies:

```json
{"ok":false,"error":{"code":"DEVICE_AUTH_REQUIRED","message":"Valid X-Device-ID and Bearer token headers are required"}}
```

HTTP 401 for missing `X-Device-ID`, missing auth, non-Bearer auth, or empty token.

```json
{"ok":false,"error":{"code":"INVALID_DEVICE_CREDENTIALS","message":"Invalid device credentials"}}
```

HTTP 401 for unknown device id or wrong token.

```json
{"ok":false,"error":{"code":"INACTIVE_DEVICE","message":"Device is inactive"}}
```

HTTP 403 for a disabled device.

## Request Schema

Top-level required fields:

| Field | Rule |
| --- | --- |
| `session_id` | Non-empty string, max 100. Scoped by authenticated device. |
| `telemetry_schema_version` | Must equal `canonical-telemetry-v2`. |
| `ecu_profile_id` | Must equal `honda_keihin_71_17`. |
| `decoder_id` | Must equal `honda_keihin_71_17`. |
| `decoder_version` | Must equal `1.0.0`. |
| `records` | Non-empty array, max `MAX_LOG_RECORDS`. |

Top-level optional fields:

| Field | Rule |
| --- | --- |
| `batch_id` | String, max 100. Diagnostic only, not identity. |
| `session_ended` | Boolean, defaults to `false`. |
| `sampling.sample_interval_ms` | Finite number >= 0. Top-level `sample_interval_ms` is also accepted. |
| `sampling.sampling_rate_hz` | Finite number >= 0. Top-level `sampling_rate_hz` is also accepted. |
| `capture_started_at` | ISO 8601 with timezone, or Unix epoch seconds. |
| `capture_ended_at` | ISO 8601 with timezone, or Unix epoch seconds. Must not be before `capture_started_at` when both are present. |
| `capture_status` | Optional only with `termination_reason`. Allowed values: `completed`, `interrupted`. |
| `termination_reason` | Optional only with `capture_status`. Allowed values: `normal_stop`, `unclean_runtime_shutdown`. |
| `analysis` | JSON object, allowed only when `session_ended` is `true`. |

There is no device identity field in the Pi sync body. Authenticated `X-Device-ID` is the device identity. Unknown top-level fields are not rejected by current code, but they are not part of the contract or persistence mapping.

## Capture Termination Metadata

The optional termination fields are a pair. Omit both for legacy-compatible uploads. When either field is supplied, both are required and only these combinations are accepted:

```json
{"capture_status":"completed","termination_reason":"normal_stop"}
```

```json
{"capture_status":"interrupted","termination_reason":"unclean_runtime_shutdown"}
```

`session_ended` reports whether this upload contains the final available transport batch. Capture termination metadata reports how local acquisition ended. They are intentionally independent: an interrupted capture may be analyzed and fully synchronized, while a final transport batch may still have sequence gaps.

Cloud persists the first authoritative metadata pair for a device/session identity and accepts identical metadata on later batches or retries. A different pair for the same identity is rejected with HTTP 409 `PI_CAPTURE_METADATA_CONFLICT`; Cloud never silently rewrites it. Existing and historical uploads without the pair retain `termination_reason = null`, which means termination is unknown rather than a confirmed normal stop.

`unclean_runtime_shutdown` means the previous acquisition runtime did not end cleanly. It does not assert that electrical power loss or ignition OFF was detected.

## Record Schema

Each `records[]` item is a combined RAW plus canonical V2 telemetry row.

Required fields:

| Field | Rule |
| --- | --- |
| `seq` | Non-negative integer. |
| `timestamp_ms` | Finite number >= 0; ride-local/source-relative milliseconds. |
| `raw_hex` | Exactly 48 hex characters, upper or lower case accepted and normalized to upper case. |
| `raw_length` | Integer exactly `24`. |
| `rpm` | Finite number, 0..100000. |
| `tps_voltage` | Finite number, 0..10. |
| `tps_raw` | Finite number, 0..255. |
| `battery_voltage` | Finite number, 0..100. |
| `iat_c` | Finite number, -80..300. |
| `ect_c` | Finite number, -80..300. |
| `frame_valid` | Boolean. |
| `checksum_valid` | Boolean. |

Optional record fields:

| Field | Rule |
| --- | --- |
| `candidate_signals` | JSON object. Defaults to `{}`. Included in the record fingerprint. |
| `quality_flags` | JSON object. Defaults to `{}`. Persisted, then Cloud adds provenance keys. Not included in the record fingerprint. |

Cloud persists Pi RAW as `native24_table17`. It does not expect `legacy29_ff5` on this endpoint and does not prepend the ESP compatibility `FF` bytes.

## RAW Contract

Cloud expects native Honda RAW24:

```text
raw_representation = native24_table17
raw_length = 24
raw_hex length = 48 hex chars
```

`raw_length == 24` is enforced. `raw_hex` byte count is enforced. Current Cloud validation is structural only: JSON type, hex shape, byte count, configured numeric ranges, and boolean fields. The Pi path does not verify Honda header bytes, recompute checksum, decode signal formulas, or compare RAW bytes to the supplied canonical values.

## Canonical Contract

Required provenance:

```text
telemetry_schema_version = canonical-telemetry-v2
ecu_profile_id = honda_keihin_71_17
decoder_id = honda_keihin_71_17
decoder_version = 1.0.0
```

Required canonical signals:

```text
rpm
tps_voltage
tps_raw
battery_voltage
iat_c
ect_c
```

Supported metadata:

- `frame_valid`: required boolean, stored as-is.
- `checksum_valid`: required boolean, stored as-is.
- `candidate_signals`: optional JSON object, stored as-is.
- `quality_flags`: optional JSON object, stored as-is plus Cloud provenance keys.

V2 does not require `map_raw`, `tps_raw_candidate`, or `ect_c_candidate`.

## Analysis Schema

`analysis` is accepted only on a final `session_ended: true` sync.

Required analysis fields:

| Field | Rule |
| --- | --- |
| `analysis_run_id` | Non-empty string, max 100. Globally unique identity. |
| `model_version` | Non-empty string, max 100. |
| `telemetry_schema_version` | Must equal `canonical-telemetry-v2`. |
| `feature_schema_version` | Must equal `ecu-window-features-v1`. |
| `signal_columns` | Must exactly equal `["rpm","tps_voltage","tps_raw","battery_voltage","iat_c","ect_c"]`. |
| `overall_status` | Non-empty string, max 100. Stored unchanged. |
| `window_count` | Non-negative integer. |
| `anomaly_window_count` | Non-negative integer, `<= window_count`. |
| `anomaly_ratio` | Finite number, 0..1. |

Optional analysis fields:

| Field | Rule |
| --- | --- |
| `health_score` | Finite number, 0..100. |
| `most_unusual_features` | Array, defaults to `[]`. |
| `warnings` | Array, defaults to `[]`; entries are stringified and truncated to 255 chars. |
| `model_loaded` | Boolean if present. |
| `evidence_window_count` | Non-negative integer. |
| `minimum_windows_for_status` | Non-negative integer. |
| `evidence_sufficient` | Boolean if present. |
| `note` | Stringified and truncated to 1000 chars. |
| `analyzed_at` | ISO 8601 with timezone, or Unix epoch seconds. If absent, storage time is used for `AnalysisResult.analyzed_at`. |
| `ecu_profile_id` | Optional; if present and non-empty, must match `honda_keihin_71_17`. |
| `decoder_id` | Optional; if present and non-empty, must match `honda_keihin_71_17`. |
| `decoder_version` | Optional; if present and non-empty, must match `1.0.0`. |

Supported current statuses are accepted and preserved:

```text
ok
monitor
attention
limited_data
no_windows
model_unavailable
not_scored
```

Current validation does not restrict `overall_status` to an enum; any non-empty string of at most 100 characters is accepted. The Web display maps the statuses above to Normal, Minor anomaly, Requires attention, Limited data, or Analysis unavailable.

## Sanitized Payload Fixture

Accepted fixture:

```text
tests/fixtures/pi_sync_valid_v2.json
```

It contains 3 records, final `session_ended: true`, native RAW24, canonical V2 signals, and a `limited_data` analysis object. Compact serialized JSON size is 1949 bytes.

## Success Response

Successful sync returns HTTP 200. Example from the fixture:

```json
{
  "ok": true,
  "device_id": "pi-contract-device",
  "session_id": "pi5-contract-session-0001",
  "received_count": 3,
  "inserted_count": 3,
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
    "analysis_run_id": "pi-contract-analysis-0001"
  },
  "server_time": "<Cloud UTC ISO timestamp>"
}
```

`analysis.state` is:

- `imported` when a new analysis row was inserted.
- `duplicate` when the same `analysis_run_id` and normalized analysis result already exists for the session.
- `null` when no analysis object was sent.

## Idempotency

Durable session identity:

```text
authenticated Device.id + session_id
```

Database uniqueness:

- `ride_sessions`: unique `(device_id, session_id)`.
- `telemetry_records`: unique `(device_id, session_id, seq)`.
- `analysis_results`: unique `analysis_run_id`.

Record identity:

```text
authenticated Device.id + session_id + seq
```

Record fingerprint:

- Algorithm: SHA-256.
- Normalization: JSON with `sort_keys=True`, compact separators, `ensure_ascii=True`.
- Included fields: `seq`, `timestamp_ms`, `raw_hex`, `raw_length`, `rpm`, `tps_voltage`, `tps_raw`, `battery_voltage`, `iat_c`, `ect_c`, `frame_valid`, `checksum_valid`, `candidate_signals`.
- Excluded fields: `quality_flags`, `batch_id`, server timestamps, database ids, request order, and any client-provided hash.

Analysis fingerprint:

- Algorithm: SHA-256.
- Normalization: JSON with `sort_keys=True`, compact separators, `ensure_ascii=True`; datetime values become ISO strings.
- Included fields: normalized analysis fields plus Cloud-normalized `session_id`, provenance, optional evidence fields, optional note/warnings/model flag.
- Excluded field: `result_fingerprint` itself.

There is no whole-payload fingerprint and no separate RAW-only or canonical-only fingerprint.

## Duplicate Behavior

Exact fixture sent twice:

First request:

```text
HTTP 200
inserted_count = 3
duplicate_count = 0
analysis.state = imported
```

Database rows after first request:

```text
ride_sessions = 1
telemetry_records = 3
analysis_results = 1
sync_batches = 1
cloud_session_id = 1
```

Second identical request:

```text
HTTP 200
inserted_count = 0
duplicate_count = 3
analysis.state = duplicate
```

Database rows after duplicate:

```text
ride_sessions = 1
telemetry_records = 3
analysis_results = 1
sync_batches = 2
cloud_session_id = 1
```

No duplicate durable session, telemetry records, or analysis result are created. A `sync_batches` audit row is created for each accepted sync attempt, including duplicates.

## Conflict Behavior

Same device, same `session_id`, same `seq`, different RAW or canonical content:

```text
HTTP 409
```

```json
{"ok":false,"error":{"code":"PI_RECORD_CONFLICT","message":"conflicting telemetry record for seq 0"}}
```

Duplicate `seq` within one batch with different content also returns `PI_RECORD_CONFLICT` with message `conflicting records for seq <n> in the same batch`.

Same device/session with an incompatible pre-existing session type returns:

```json
{"ok":false,"error":{"code":"PI_SESSION_CONFLICT","message":"session_id already exists with a different telemetry schema"}}
```

or the corresponding `source_type`/legacy-record message.

Same `analysis_run_id`, same session, different normalized analysis result:

```text
HTTP 409
```

```json
{"ok":false,"error":{"code":"PI_ANALYSIS_CONFLICT","message":"analysis_run_id conflicts with an existing result"}}
```

Same `analysis_run_id` attached to another Cloud session also returns `PI_ANALYSIS_CONFLICT`.

Conflicting capture termination metadata for the same authenticated device and `session_id` returns:

```json
{"ok":false,"error":{"code":"PI_CAPTURE_METADATA_CONFLICT","message":"capture metadata conflicts with the existing session identity"}}
```

## Timestamp Semantics

Record `timestamp_ms` is ride-local/source-relative milliseconds. Cloud stores it in both:

- `TelemetryRecord.timestamp_ms` as float.
- `TelemetryRecord.device_time_ms` as `round(timestamp_ms)` integer.

Cloud does not treat record `timestamp_ms` as Unix time. `TelemetryRecord.timestamp` is stored as `null` for Pi sync.

Session wall-clock metadata:

- `capture_started_at` and `capture_ended_at` are optional wall-clock timestamps.
- `started_at` uses `capture_started_at` when supplied, otherwise Cloud ingest time for the first session row.
- `ended_at` uses `capture_ended_at` when supplied on a final sync.
- `server_received_at` and `last_record_at` are Cloud ingest/audit time.
- `duration_ms` is recomputed from telemetry as `max(timestamp_ms) - min(timestamp_ms)`.

## Record Ordering

Current validation:

- Does not require records to be sorted.
- Does not require monotonic `seq` within the request.
- Does not require monotonic `timestamp_ms`.
- Does not require `seq` to start at 0.
- Deduplicates identical repeated `seq` values in one batch.
- Rejects conflicting repeated `seq` values in one batch.
- Does not compare record count against a separate canonical/RAW count because RAW and canonical data are in the same record.

ACKs report stored sequence coverage for the authenticated device/session:

```json
{"minimum":0,"maximum":2,"contiguous_until":2}
```

On `session_ended: true`, `sync_status=complete` only when stored sequences are contiguous from the stored minimum through the stored maximum. Otherwise `sync_status=partial` and `capture_status=completed_with_gaps`.

## Lineage Validation

Current Cloud checks:

- Authenticated device owns the session identity.
- Top-level Pi provenance must exactly match canonical V2 Honda profile/version.
- Existing records with the same `(device, session_id, seq)` must match the record fingerprint.
- Optional analysis provenance, if present, must match the session provenance.
- `analysis_run_id` must be globally unique unless the normalized analysis result is identical for the same Cloud session.

Current Cloud does not check:

- RAW bytes semantically decode to the supplied canonical values.
- `frame_valid` or `checksum_valid` by recomputing the ECU checksum.
- Analysis result is derived from a specific telemetry record fingerprint.
- Separate RAW to canonical one-to-one mapping, because the endpoint has one combined record array.

## Size Limits

Configured application limits:

- `MAX_CONTENT_LENGTH`: default 2,097,152 bytes. API 413 body is `{"ok":false,"error":{"code":"REQUEST_TOO_LARGE","message":"Request body is too large"}}`.
- `MAX_LOG_RECORDS`: default 1000 records per request.

Deployment limits found:

- Docker Compose passes `MAX_CONTENT_LENGTH=${MAX_CONTENT_LENGTH:-2097152}` and `MAX_LOG_RECORDS=${MAX_LOG_RECORDS:-1000}`.
- Nginx example sets `client_max_body_size 2m`.
- Nginx Proxy Manager advanced config sets `client_max_body_size 2m`.
- Gunicorn command sets worker/thread counts and timeouts but no separate request byte limit.

Database field limits relevant to Pi sync:

- `session_id`, `batch_id`, `analysis_run_id`, `model_version`: max 100 at validation.
- `raw_hex`: database column length 512; Pi validation requires exactly 48 chars.
- JSON fields are constrained by request size and database JSON storage, not endpoint-specific per-field byte limits.

Largest current Pi sync test payload:

- Real HTTP flow sends 60 records as three requests of 20 records each.
- Batch 1: 20 records, 5577 compact JSON bytes, HTTP 200.
- Batch 2: 20 records, 5593 compact JSON bytes, HTTP 200.
- Batch 3: 20 records plus analysis, 6105 compact JSON bytes, HTTP 200.

This is not a maximum-size stress test. Staging E2E should include payloads close to real ride sizes and verify batching below 2 MiB and 1000 records/request.

## Transaction Semantics

`store_pi_sync` performs all session, telemetry, analysis, batch, and device `last_seen_at` updates in one SQLAlchemy session and calls `db.session.commit()` at the end.

The route catches validation and SQLAlchemy failures, calls `db.session.rollback()`, and returns an error. A failure before commit should roll back the partial session/record/analysis work for that request. After a 500, Pi can safely resend the same payload; if the previous transaction did commit but the response was lost, idempotency returns duplicates instead of creating duplicate durable telemetry.

## HTTP Classification For Pi

| HTTP | Actual Cloud semantics | Pi guidance |
| --- | --- | --- |
| 400 | Malformed/non-object JSON, pairing-code format errors, CSRF on non-exempt APIs. | Permanent until request body/client bug is fixed. |
| 401 | Missing/invalid device auth headers, unknown device, wrong/rotated token. | Permanent until credentials are corrected. |
| 403 | Device inactive or disabled owning user during pairing. | Permanent until operator re-enables/fixes account. |
| 404 | Not returned by Pi sync for valid route; dashboard/readback APIs can return 404 for missing/unauthorized browser resources. | Permanent for that URL/id. |
| 408 | No explicit application 408 behavior found. | Treat transport timeout/no response as retryable with same payload. |
| 409 | Pi session, record, or analysis identity conflict; duplicate device id or pairing-code reuse during provisioning. | Permanent data/identity conflict; do not mutate and retry blindly. |
| 413 | Request exceeds Flask/proxy body limit. | Retry only after reducing batch size. |
| 415 | Content-Type is not `application/json`. | Permanent client bug. |
| 422 | Schema/provenance/range/field validation error. | Permanent until payload is corrected. |
| 429 | Pairing attempt limit or live telemetry rate limit. Pi sync endpoint itself has no rate limiter. | For pairing/live obey policy/Retry-After; for sync no current app 429. |
| 5xx | Storage failure, backend outage, proxy/app failure. | Retry same payload with backoff. |

## Persistence Mapping

Successful Pi sync populates or updates:

| Model/table | Data stored |
| --- | --- |
| `Device` / `devices` | `last_seen_at` updated; device identity comes from auth. |
| `RideSession` / `ride_sessions` | `device_id`, `session_id`, `started_at`, `ended_at`, `last_record_at`, `telemetry_schema_version`, `source_type=pi_native`, `raw_representation=native24_table17`, `transport_profile_id=native24_table17`, `ecu_profile_id`, `decoder_id`, `decoder_version`, capture timestamps, `duration_ms`, sampling metadata, `record_count`, first/last seq, sync/capture/analysis status, counts, `analysis_run_id`. |
| `TelemetryRecord` / `telemetry_records` | One row per unique `seq`: RAW24 `raw_frame` and `raw_hex`, `raw_length`, `raw_representation`, canonical V2 signals, `timestamp_ms`, `device_time_ms`, flags, metadata JSON, `record_fingerprint`. |
| `AnalysisResult` / `analysis_results` | Pi analysis provenance and status: `analysis_run_id`, model/feature/schema fields, decoder provenance, `signal_columns`, `overall_status`, `health_score`, `anomaly_ratio`, `anomaly_window_count`, full `result_summary`, `result_fingerprint`, `analyzed_at`. |
| `SyncBatch` / `sync_batches` | Batch audit row: `batch_id`, first/last seq, received/inserted/duplicate counts, received time, status. |
| `EcuProfile` / `ecu_profiles` | Ensures `honda_keihin_71_17` profile exists. |
| `DecoderVersion` / `decoder_versions` | Ensures `honda_keihin_71_17:1.0.0` exists and is marked production. |

`RawArtifact` is not populated by Pi sync.

## Readback And Web Display

Available readback paths after upload:

- `GET /sessions/<session_pk>.json`: session summary, statistics, analysis, and raw records.
- `GET /api/sessions/<session_pk>/samples`: chart payload, parameters, samples, statistics, events.
- `GET /api/sessions/<session_pk>/analysis`: stored/external analysis snapshot.
- `GET /sessions/<session_pk>.csv`: CSV export with V2 columns and RAW.

The current Web payload includes and displays V2 signals:

```text
rpm
tps_voltage
tps_raw
battery_voltage
iat_c
ect_c
```

`monitor` displays as `Monitor`. `limited_data` displays as `Limited data`. Technical statuses `no_windows`, `model_unavailable`, and `not_scored` display as `Analysis unavailable`.

Pi uploaded records do not need legacy `map_raw`; tests assert `map_raw` is not advertised when no records contain it.

## Staging Readback Evidence (2026-09-29)

Sanitized staging verification used `https://drivesafe.top` with device `ecuread-pi5-001` and vehicle `Honda SH Mode 125`. The checks below were read-only against Cloud persistence and readback routes using the patched checkout in a one-off web container connected to the staging database; no Pi hardware, credentials, payload hashes, or bearer tokens are documented here. The long-running web container must be rebuilt or recreated before the public process serves the updated `Monitor` display label.

Short ride:

```text
session_id = 970bc023-bbb4-5a5e-8f92-cfc1cb7844ce
cloud_session_pk = 214
device-scoped session rows = 1
global session rows = 1
telemetry rows = 87
record_count = 87
seq range = 0..86
missing seq count = 0
duplicate seq count = 0
duration_ms = 21499.0
source_type = pi_native
raw_representation = native24_table17
telemetry_schema_version = canonical-telemetry-v2
sync_status = complete
capture_status = completed
analysis_status = completed
analysis_results = 1
overall_status = limited_data
analysis_run_id = 7a1793b2-ebf2-42c7-a691-8ec8bec0d223
window_count = 4
anomaly_window_count = 4
health_score = 17.63
```

Short ride sync audit:

```text
batch 00000: received=87 inserted=87 duplicate=0 status=accepted
duplicate probe for batch 00000: received=87 inserted=0 duplicate=87 status=accepted
```

Large ride:

```text
session_id = c26dd936-9911-5678-b2a7-7fbd68a7d79f
cloud_session_pk = 216
device-scoped session rows = 1
global session rows = 1
telemetry rows = 1290
record_count = 1290
seq range = 0..1289
missing seq count = 0
duplicate seq count = 0
duration_ms = 322249.0
source_type = pi_native
raw_representation = native24_table17
telemetry_schema_version = canonical-telemetry-v2
sync_status = complete
capture_status = completed
analysis_status = completed
analysis_results = 1
overall_status = monitor
analysis_run_id = c652ee97-ffd6-4e89-9f07-a3fe46ac0403
window_count = 125
anomaly_window_count = 6
anomaly_ratio = 0.048
health_score = 86.77
```

Large ride sync audit:

```text
batch 00000: first_seq=0 last_seq=999 received=1000 inserted=1000 duplicate=0 status=accepted
batch 00001: first_seq=1000 last_seq=1289 received=290 inserted=290 duplicate=0 status=accepted
```

Both rides persisted all six canonical V2 signals for every telemetry row:

```text
rpm
tps_voltage
tps_raw
battery_voltage
iat_c
ect_c
```

`map_raw` had zero non-null values and no candidate MAP-like keys. Readback checks covered `/sessions/<session_pk>.json`, `/api/sessions/<session_pk>/samples`, `/api/sessions/<session_pk>/analysis`, `/sessions/<session_pk>.csv`, and `/sessions/<session_pk>` HTML. JSON/API payloads expose the six V2 signals; CSV exports include the V2 columns and retain a blank `map_raw` compatibility column. HTML pages display `Limited data` for the short ride and `Monitor` for the large ride, without inventing MAP values.

## Cloud Re-Analysis And Decoder Authority

For valid Pi uploads containing RAW plus canonical plus analysis, Cloud does not call:

```text
Analyzer
/api/v1/analysis
/api/v1/raw/decode-analyze
Honda semantic decoder
```

The Pi sync handler imports only `store_pi_sync` and `validate_pi_sync`. `services/pi_sync.py` does not import `AnalyzerClient` or `ecu.decoder`. Manual browser re-run for `source_type=pi_native` returns the imported Pi result and message `Pi-native analysis is imported from the device and is not rerun by Cloud.`

Active production distinction:

- Pi path: structural validation plus persistence only.
- ESP durable path: stores RAW29 compatibility records, then `services.analysis_submission` can submit ESP RAW to external Analyzer if configured.
- Legacy local Honda formulas still exist in `ecu/decoder.py` and `ecu/profiles/honda_keihin_legacy_29.py`, but those files declare themselves legacy/not active for new ingestion and are not imported by active ingest routes.
- Tools, docs, tests, and mock analyzer contain Honda formulas for simulation and contract tests, not Pi production decode.

No architecture violation was found for the Pi path.

## Provisioning

Supported staging procedure:

1. User/admin logs into the Web dashboard.
2. Generate a pairing code with `POST /devices/pairing-codes` or the Devices page. Pairing code is returned once and expires by `PAIRING_CODE_TTL_SECONDS`.
3. Pi calls `POST /api/device/pair` with:

```json
{
  "pairing_code": "<one-time code>",
  "device_id": "<stable Pi device id>",
  "device_name": "<display name>",
  "vehicle_name": "<display vehicle>",
  "ecu_type": "Honda Keihin 0x71 0x17",
  "firmware_version": "<optional>",
  "hardware_version": "raspberry-pi-5"
}
```

4. Cloud returns HTTP 201 with `device.device_id` and one-time `device_token`.
5. Pi stores `device_id` and `device_token`. Cloud stores only the token hash.

Dashboard/manual alternatives:

- `POST /api/devices` can create a device and return a token for a logged-in user.
- `POST /api/devices/<device_pk>/rotate-token` rotates a token.
- `/devices/<device_pk>/disable` or `/api/devices/<device_pk>/disable` disables a device.

Do not place real tokens in docs, logs, or fixtures.

## Staging Values Needed By Pi

Pi needs:

- Cloud base URL, for example the intended staging/production host.
- `device_id`.
- `device_token`.
- TLS policy for that base URL.
- Endpoint path: `/api/device/sync/session`.

Current repo examples point production traffic at:

```text
https://drisafe.haithinh.top
```

This audit did not find a separate staging URL in repository config. If staging differs, the operator must provide that exact base URL before real Pi staging sync.

Configuration check before staging: make sure `TRUSTED_HOSTS` includes the selected public host. The README and proxy examples use `drisafe.haithinh.top`, while `config.py` and `docker-compose.yml` have a production default of `drivesafe.top` when the environment does not override it.

## TLS

Production config expects HTTPS at the public edge:

- `Config.PREFERRED_URL_SCHEME` is `https` in production.
- `SESSION_COOKIE_SECURE` defaults true in production.
- Example Nginx config listens on 443 with Let's Encrypt cert paths and redirects port 80 to HTTPS.
- Nginx Proxy Manager notes say Cloudflare supplies browser-facing HTTPS while the tunnel reaches NPM over local HTTP.
- The app container itself listens on plain HTTP port 5000 behind the reverse proxy.

Pi should use normal public CA validation for the public HTTPS base URL. Plain HTTP is only for internal local proxy/container hops or local development.

## Tests

Current Pi sync coverage:

- `tests.test_app.DashboardTestCase.test_pi_pairing_reuses_existing_device_pair_endpoint`
- `tests.test_app.DashboardTestCase.test_pi_v2_sync_multibatch_retry_conflict_final_analysis_and_browser_payloads`
- `tests.test_app.DashboardTestCase.test_pi_finalization_with_gaps_uses_relative_duration`
- `tests.test_app.DashboardTestCase.test_pi_sync_rejects_invalid_provenance_nonfinite_and_keeps_device_scope`
- `tests.test_app.DashboardTestCase.test_pi_sync_valid_v2_fixture_is_accepted`
- `tests.test_app.DashboardTestCase.test_pi_analysis_statuses_are_accepted_preserved_and_displayed`
- `tests.http_flow.test_real_http_flows.RealHttpFlowTestCase.test_esp_to_analyzer_and_pi_to_cloud_real_http_flows`
- `tests.http_flow.test_real_http_flows.FrameFactoryTestCase.test_native_fixture_checksum_decode_and_legacy_conversion`

Verification run:

```text
.venv/bin/python -m unittest
Ran 49 tests in 28.457s
OK
```

The full suite emitted ResourceWarning messages for unclosed SQLite connections from test server/database setup, but all tests passed.

## Staging Blockers

No Cloud code blocker was found for first Pi staging sync.

Operational inputs still required before staging:

- Confirm the staging base URL, if different from `https://drisafe.haithinh.top`.
- Confirm `TRUSTED_HOSTS` matches the selected public host before staging.
- Provision or generate a real Pi `device_id` and one-time token.
- Confirm Pi request batches stay below 2 MiB and 1000 records/request, or configure smaller client batches.
- Run a real ride-size staging E2E because the largest current test request is only 20 records.
