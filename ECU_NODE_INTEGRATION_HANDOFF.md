# ECU Node To Server/Web Integration Handoff

Date: 2026-08-26

This handoff documents the existing DriSafe server/web contract for a separate ECU Reader node repository. It does not implement the node and does not redesign the backend. Anything marked "missing" or "ambiguous" is not currently a backend contract.

## Inspected Scope

Relevant source inspected:

- Device ingest routes: `routes/ingest.py`
- Pairing/provisioning service: `services/provisioning.py`
- Durable telemetry storage: `services/telemetry.py`
- Live Preview cache/validation: `services/live.py`
- Live Mode control commands: `services/live_control.py`
- Database models and migrations: `models.py`, `migrations/versions/*`
- Dashboard/session/live routes: `routes/dashboard.py`
- Dashboard analysis and formatting: `services/analysis.py`
- ECU raw parser/decoder/profile/checksum: `ecu/*`
- Frontend live/session/tool JS/templates: `templates/*`, `static/js/*`
- Transport/deployment notes: `README.md`, `docker-compose.yml`, `deploy/*`, `mqtt_service.py`
- Tests: `tests/test_app.py`

## Executive Summary

- Device-to-server communication is HTTPS JSON. Live Mode remote control uses authenticated HTTP polling; there is no device WebSocket or MQTT ingest/control transport.
- A user generates a short-lived server-side pairing code in the dashboard. The node consumes it once at `POST /api/device/pair`.
- After pairing, the node must persist exactly two server credentials for device API calls: `device_id` and `device_token`.
- Device API authentication is:

```http
X-Device-ID: <device_id>
Authorization: Bearer <device_token>
```

- The canonical durable upload endpoint for ECU node sessions is:

```text
POST /api/logs/upload
Content-Type: application/json
```

- The durable upload body is a JSON object, not multipart, not CSV, and not JSONL over HTTP.
- Durable telemetry rows are idempotent at record level by `(device database id, session_id, seq)`.
- The node must only delete local records after a successful durable acknowledgement and only through `accepted_sequences.contiguous_until`.
- Live Preview is optional, temporary, not stored in ride history, rate-limited, and must never be used as a durable-upload acknowledgement.
- Live Mode enable/disable commands are runtime-only events targeted to the node's current `boot_id`; there is no durable desired-live boolean that survives reboot.
- The backend owns RAW ECU decoding when `raw_frame` is present. Frontend code formats canonical fields; it does not implement the authoritative byte decoder.

## Endpoint Matrix

Device-facing endpoints:

| Purpose | Method/path | Auth | Persistent |
| --- | --- | --- | --- |
| Pair new node | `POST /api/device/pair` | Pairing code in JSON body | Creates `Device` |
| Durable batch upload | `POST /api/logs/upload` | `X-Device-ID` + bearer token | Yes |
| Single development record | `POST /api/telemetry` | `X-Device-ID` + bearer token | Yes |
| Temporary live preview | `POST /api/telemetry/live` | `X-Device-ID` + bearer token | No |
| Poll Live Mode command | `POST /api/device/control/poll` | `X-Device-ID` + bearer token | Runtime command state |
| Acknowledge Live Mode command | `POST /api/device/control/ack` | `X-Device-ID` + bearer token | Runtime command state |

Browser/user endpoints relevant to node operation:

| Purpose | Method/path | Auth |
| --- | --- | --- |
| Generate pairing code | `POST /devices/pairing-codes` | Logged-in dashboard user + CSRF |
| Revoke pairing code | `POST /devices/pairing-codes/<code_pk>/revoke` | Logged-in dashboard user + CSRF |
| Manual device creation | `POST /api/devices` or `POST /devices` | Logged-in dashboard user + CSRF |
| Rotate token | `POST /api/devices/<device_pk>/rotate-token` or `POST /devices/<device_pk>/rotate-token` | Logged-in dashboard user + CSRF |
| Disable device | `POST /api/devices/<device_pk>/disable` or `POST /devices/<device_pk>/disable` | Logged-in dashboard user + CSRF |
| Live latest sample | `GET /api/devices/<device_id>/live/latest` | Logged-in dashboard user |
| Live SSE stream | `GET /api/devices/<device_id>/live/stream` | Logged-in dashboard user |
| Live control status | `GET /api/devices/<device_id>/live/control` | Logged-in dashboard user |
| Enable Live Mode | `POST /api/devices/<device_id>/live/enable` | Logged-in dashboard user + CSRF |
| Disable Live Mode | `POST /api/devices/<device_id>/live/disable` | Logged-in dashboard user + CSRF |
| Session JSON export | `GET /sessions/<session_pk>.json` | Logged-in dashboard user |
| Session CSV export | `GET /sessions/<session_pk>.csv` | Logged-in dashboard user |

## Pairing And Device Identity

### Pairing Code Generation

The server generates pairing codes. The node does not generate them.

Current generation flow:

- User logs in to the dashboard.
- User calls the Devices page action `POST /devices/pairing-codes`.
- Server generates a 16-character code from `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`.
- Server formats it in groups of four, for example `ABCD-EFGH-JKLM-NPQR`.
- Server stores only `sha256(normalized_code)` in `device_pairing_codes.code_hash`.
- Normalization uppercases and removes hyphens/whitespace.
- Default TTL is `PAIRING_CODE_TTL_SECONDS=900` seconds.
- Default maximum attempts for a known code is `PAIRING_MAX_ATTEMPTS=10`.
- The code is one-time-use. Successful pairing sets `used_at` and links the pairing-code row to the created device row.
- Revoking a code also sets `used_at`.

JSON generation response, when requested as JSON:

```http
POST /devices/pairing-codes
Cookie: <dashboard session>
X-CSRFToken: <csrf token>
Accept: application/json
```

```json
{
  "pairing_code": "ABCD-EFGH-JKLM-NPQR",
  "expires_at": "2026-08-26T12:15:00+00:00",
  "id": 123
}
```

Status is `201`. The response sets `Cache-Control: no-store`.

### Node Pairing Request

The node consumes a pairing code at:

```http
POST /api/device/pair
Content-Type: application/json
```

No bearer token is used for pairing.

Required JSON fields:

| Field | Source | Constraints |
| --- | --- | --- |
| `pairing_code` | User enters server-generated code into node setup | String, non-empty after normalization |
| `device_id` | Node or setup portal chooses it | 3 to 64 chars, first char alphanumeric, then letters/numbers/dot/colon/underscore/hyphen |
| `device_name` | Node setup/user | Non-empty string, max 100 chars |
| `vehicle_name` | Node setup/user | Non-empty string, max 100 chars |
| `ecu_type` | Node setup/user | Non-empty string, max 100 chars |

Optional JSON fields:

| Field | Constraints |
| --- | --- |
| `firmware_version` | String max 100 chars, `null`/empty allowed |
| `hardware_version` | String max 100 chars, `null`/empty allowed |

Example:

```json
{
  "pairing_code": "ABCD-EFGH-JKLM-NPQR",
  "device_id": "ecu-abc123",
  "device_name": "ECU Reader",
  "vehicle_name": "Test Motorcycle",
  "ecu_type": "honda_kline",
  "firmware_version": "1.0.0",
  "hardware_version": "xiao-esp32s3"
}
```

Successful response:

```http
HTTP/1.1 201 Created
Cache-Control: no-store
Content-Type: application/json
```

```json
{
  "ok": true,
  "device": {
    "device_id": "ecu-abc123",
    "device_name": "ECU Reader",
    "vehicle_name": "Test Motorcycle",
    "ecu_type": "honda_kline",
    "firmware_version": "1.0.0",
    "hardware_version": "xiao-esp32s3"
  },
  "device_token": "SERVER_GENERATED_ONE_TIME_VISIBLE_SECRET"
}
```

The database stores `Device.token_hash`, not the plaintext token. The plaintext `device_token` is returned once.

### Pairing Errors

Errors use:

```json
{
  "ok": false,
  "error": {
    "code": "ERROR_CODE",
    "message": "Human readable message"
  }
}
```

Known pairing error cases:

| HTTP | Code | When |
| --- | --- | --- |
| 400 | `MALFORMED_JSON` | Body is not a JSON object |
| 400 | `INVALID_PAIRING_CODE` | Missing/blank/unknown pairing code |
| 400 | `PAIRING_CODE_EXPIRED` | Code exists but expired |
| 403 | `USER_DISABLED` | Code owner account is disabled |
| 409 | `PAIRING_CODE_USED` | Code already used or revoked |
| 409 | `DUPLICATE_DEVICE_ID` | `device_id` already exists |
| 422 | `INVALID_DEVICE_METADATA` | Required metadata missing, too long, or invalid `device_id` format |
| 429 | `PAIRING_ATTEMPTS_EXCEEDED` | Known code exceeded configured attempt limit |

### Identity The Node Must Persist

Persist after successful pairing:

| Value | Generated by | Sensitive | Must survive reboot | Notes |
| --- | --- | --- | --- | --- |
| `device_id` | Node/setup input | No, but identifies device | Yes | Public device identifier used in `X-Device-ID` |
| `device_token` | Server | Yes | Yes | Bearer credential; cannot be recovered from server later |
| Backend base URL | Node configuration | No | Yes | Production is documented as `https://drisafe.haithinh.top` |

Not returned to the node by pairing:

- `account_id`
- `user_id`
- `vehicle_id`
- device database primary key `id`
- refresh token
- command queue identifier

The current system associates the device to a dashboard user through the pairing-code owner. The node does not send or store an account/user id.

### Authentication After Pairing

All later device calls to ingest endpoints require:

```http
X-Device-ID: ecu-abc123
Authorization: Bearer DEVICE_TOKEN
```

Details:

- `Authorization` scheme is case-insensitive, but use `Bearer`.
- Token has no expiration and no refresh endpoint in current code.
- Token rotation is a dashboard/user action. The new token is returned to the browser once and the old token becomes invalid immediately.
- There is no current device-side token refresh or automatic token delivery mechanism.
- If a device is disabled in the dashboard, all device ingest endpoints return `403`.

Auth error cases:

| HTTP | Code | When |
| --- | --- | --- |
| 401 | `DEVICE_AUTH_REQUIRED` | Missing `X-Device-ID`, missing bearer header, malformed bearer header |
| 401 | `INVALID_DEVICE_CREDENTIALS` | Unknown `device_id` or bad token |
| 403 | `INACTIVE_DEVICE` | Device exists but is disabled |

## Durable Session Upload Contract

### Canonical Endpoint

Use this for offline ride sessions:

```http
POST /api/logs/upload
Content-Type: application/json
X-Device-ID: <device_id>
Authorization: Bearer <device_token>
```

The request body must be a JSON object. The current backend does not accept multipart files, CSV files, or JSONL files directly over HTTP.

Default request limits:

- `MAX_CONTENT_LENGTH=2097152` bytes for the whole HTTP body.
- `MAX_LOG_RECORDS=1000` records per batch.
- `RAW_FRAME_MAX_BYTES=16384` bytes for the JSON-encoded `raw_frame` value per record.

### Top-Level Payload

Required:

| Field | Type | Meaning |
| --- | --- | --- |
| `session_id` | string, max 100 | Node-generated stable ride/session id |
| `records` | non-empty array | Telemetry records for this session |

Optional:

| Field | Type | Meaning |
| --- | --- | --- |
| `session_ended` | boolean, default `false` | Set `true` on final batch for the session |
| `batch_id` | string, max 100 | Stored in `sync_batches` for diagnostics only; not unique/idempotent |
| `session_started_at` | timestamp | Optional wall-clock start time |
| `sample_interval_ms` | finite number >= 0 | Session sampling metadata |
| `sampling_rate_hz` | finite number >= 0 | Session sampling metadata |
| `ecu_profile_id` | string, max 100 | Optional profile key; defaults to `honda_keihin_legacy_29` |

Canonical example:

```json
{
  "session_id": "ecu-abc123-20260826T120000Z",
  "session_started_at": "2026-08-26T12:00:00Z",
  "session_ended": false,
  "batch_id": "ecu-abc123-20260826T120000Z-0001",
  "sample_interval_ms": 100,
  "ecu_profile_id": "honda_keihin_legacy_29",
  "records": [
    {
      "seq": 0,
      "device_time_ms": 0,
      "raw_frame": "FFFFFFFFFF0218711700001900FFFF81495C597D0000587C0000000077"
    },
    {
      "seq": 1,
      "device_time_ms": 100,
      "raw_frame": "FFFFFFFFFF0218711700001900FFFF81495C597D0000587C0000000077"
    }
  ]
}
```

Final-batch example:

```json
{
  "session_id": "ecu-abc123-20260826T120000Z",
  "session_ended": true,
  "batch_id": "ecu-abc123-20260826T120000Z-final",
  "sample_interval_ms": 100,
  "ecu_profile_id": "honda_keihin_legacy_29",
  "records": [
    {
      "seq": 2381,
      "device_time_ms": 238100,
      "raw_frame": "FFFFFFFFFF0218711700001900FFFF81495C597D0000587C0000000077"
    }
  ]
}
```

### Record Payload

Required per record:

| Field | Type | Meaning |
| --- | --- | --- |
| `seq` | non-negative integer | Per-session sequence number; used for idempotency and acknowledgement |

Optional per record:

| Field | Type/constraints |
| --- | --- |
| `session_id` | May be omitted. If present, must match top-level `session_id` |
| `timestamp` | Timezone-aware ISO 8601 string or Unix epoch seconds |
| `device_time_ms` | Non-negative integer |
| `timestamp_ms` | Finite number >= 0 |
| `rpm` | Number 0..100000 |
| `tps` | Number 0..100 |
| `ect` | Number -80..300 |
| `iat` | Number -80..300 |
| `battery` | Number 0..100 |
| `tps_voltage` | Number 0..10 |
| `tps_raw_candidate` | Number 0..255 |
| `battery_voltage` | Number 0..100 |
| `iat_c` | Number -80..300 |
| `ect_c_candidate` | Number -80..300 |
| `map_raw` | Number 0..255 |
| `injector_ms` | Number 0..1000 |
| `ignition_deg` | Number -180..180 |
| `raw_frame` | Hex string or JSON object metadata, described below |
| `candidate_signals` | JSON object |
| `quality_flags` | JSON object |
| `frame_valid` | boolean, defaults `true` before backend decode |
| `checksum_valid` | boolean, defaults `true` before backend decode |
| `decoder_valid` | boolean, defaults `true` before backend decode |

Unknown top-level or record fields are currently ignored for durable uploads, except unknown keys inside `raw_frame` object metadata are preserved if JSON-serializable. Do not rely on ignored fields as a stable contract.

### Timestamps

Current timestamp parsing:

- `timestamp` and `session_started_at` may be Unix epoch seconds as JSON number.
- String timestamps must be ISO 8601 and include timezone, for example `2026-08-26T12:00:00Z` or `2026-08-26T12:00:00+00:00`.
- Naive timestamps without timezone are rejected.
- `device_time_ms` is accepted when the node lacks wall-clock time.
- Server always records `server_received_at`.

Session event time for statistics and ordering is `timestamp` if present, otherwise `server_received_at`.

### RAW Frame Format

Durable `raw_frame` may be one of:

1. Compact hex string:

```json
"FFFFFFFFFF0218711700001900FFFF81495C597D0000587C0000000077"
```

2. Separated byte string:

```json
"FF;FF;FF;FF;FF;02;18;71;17;00;00;19;00;FF;FF;81;49;5C;59;7D;00;00;58;7C;00;00;00;00;77"
```

3. Metadata object containing one of `hex`, `raw_hex`, or `frame_hex`:

```json
{
  "hex": "FFFFFFFFFF0218711700001900FFFF81495C597D0000587C0000000077",
  "frame_type": "sample",
  "checksum_ok": true,
  "parser_state": "locked"
}
```

Validation rules:

- The value must not be empty.
- Compact form must contain an even number of hex characters.
- Separated form may use semicolon, comma, or whitespace between 1-2 digit hex byte tokens.
- Hex is normalized to uppercase before storage.
- Metadata objects must include at least one non-null `hex`, `raw_hex`, or `frame_hex` value.
- Metadata objects must be JSON-serializable.
- Malformed hex is rejected with `422 VALIDATION_ERROR`.
- Wrong ECU length or failed checksum is not rejected at upload time. It is stored and marked invalid by backend decode.

Canonical node guidance: upload compact uppercase 29-byte frames as a string, or as `{"hex": "<58 hex chars>", ...}` only when extra parser metadata is valuable. If the node stores JSONL locally, convert it to this JSON batch shape before HTTP upload.

### Backend RAW Decoder

Default and only registered profile:

```text
ecu_profile_id: honda_keihin_legacy_29
manufacturer: Honda
protocol_family: honda_kline
frame_length: 29 bytes
decoder_id: honda_keihin_legacy_29
decoder_version: 0.1.0
telemetry_schema_version: canonical-telemetry-v1
checksum: sum(frame) % 256 == 251
```

The decoder uses zero-based byte indexes:

| Signal | Formula | Unit/status |
| --- | --- | --- |
| `rpm` | `(b9 << 8) | b10` | rpm, high-confidence candidate |
| `tps_voltage` | `b11 * 5 / 256` | V, high-confidence candidate |
| `tps_raw_candidate` | `b12` | raw, candidate |
| `battery_voltage` | `b15 / 10` | V, high-confidence candidate |
| `iat_c` | `b16 - 40` | degC, high-confidence candidate |
| `ect_c_candidate` | `b17 - 40` | degC, candidate |
| `map_raw` | `b18` | raw, candidate |
| `signal_b19` | `b19` | stored in `candidate_signals` |
| `signal_word_20_21` | `(b20 << 8) | b21` | stored in `candidate_signals` |
| `signal_b22` | `b22` | stored in `candidate_signals` |
| `signal_b23` | `b23` | stored in `candidate_signals` |
| `signal_b24` | `b24` | stored in `candidate_signals` |

Decoded signal validity limits:

| Signal | Valid range |
| --- | --- |
| `rpm` | 0..16000 |
| `tps_voltage` | 0..5 |
| `tps_raw_candidate` | 0..255 |
| `battery_voltage` | 6..18 |
| `iat_c` | -40..150 |
| `ect_c_candidate` | -40..180 |
| `map_raw` | 0..255 |

When `raw_frame` is present, the backend applies authoritative decode before storing. It overwrites decoded canonical fields from the raw frame where available:

- `rpm`
- `tps_voltage`
- `tps_raw_candidate`
- `battery_voltage`
- `iat_c`
- `ect_c_candidate`
- `map_raw`
- legacy display aliases `battery`, `iat`, and `ect`
- `frame_valid`
- `checksum_valid`
- `decoder_valid`
- decoder provenance in `quality_flags`
- candidate signals in `candidate_signals`

Important gaps:

- The backend does not currently decode TPS percent (`tps`) from the Honda raw frame. It only decodes `tps_voltage` and `tps_raw_candidate`.
- The backend does not currently decode MAP pressure units, only `map_raw`.
- The backend does not currently decode `injector_ms`, `injector_raw`, or `ignition_deg` from raw frames. Durable upload can store `injector_ms` and `ignition_deg` only if the node supplies them. `injector_raw` exists only in Live Preview validation/display.

### Successful Durable Upload Response

`POST /api/logs/upload` returns HTTP `200` on success, even when it creates a new session.

Example:

```json
{
  "ok": true,
  "device_id": "ecu-abc123",
  "session_id": "ecu-abc123-20260826T120000Z",
  "received_count": 2,
  "inserted_count": 2,
  "duplicate_count": 0,
  "accepted_sequences": {
    "minimum": 0,
    "maximum": 1,
    "contiguous_until": 1
  },
  "server_time": "2026-08-26T12:00:05.123456Z"
}
```

Acknowledgement meanings:

- `received_count`: number of records in the submitted request body.
- `inserted_count`: number of new `telemetry_records` inserted.
- `duplicate_count`: `received_count - inserted_count`. This includes already-stored records and repeated `seq` values inside the same request.
- `accepted_sequences.minimum`: lowest stored sequence for this `(device, session_id)`.
- `accepted_sequences.maximum`: highest stored sequence for this `(device, session_id)`.
- `accepted_sequences.contiguous_until`: highest stored sequence with no gap from the stored minimum.

Example: if the server has stored `0, 1, 2, 4`, then `contiguous_until` is `2`, not `4`.

### Local Deletion Rule For Node Firmware

The node must never delete local durable data based on live-preview responses.

For durable uploads, the safest existing rule is:

```text
Delete local records only when:
1. HTTP status is 200 from POST /api/logs/upload.
2. Response JSON parses.
3. response.ok is true.
4. response.device_id equals the node's persisted device_id.
5. response.session_id equals the uploaded local session_id.
6. response.accepted_sequences.contiguous_until is an integer.
7. Delete only records in that session with seq <= contiguous_until.
```

For deleting an entire local session file:

```text
Delete the whole session only after the node has uploaded the final batch with session_ended=true and a successful acknowledgement has contiguous_until >= the session's final seq.
```

Do not use `inserted_count` alone. A retry can return `inserted_count: 0` because the data was already safely stored.

Node-side sequence recommendation:

- Start every new session at `seq=0` or `seq=1` consistently.
- Never reuse a `(session_id, seq)` pair for different raw data.
- Keep `session_id` stable across all batches and retries for one ride.
- Avoid gaps. If gaps happen, keep all local records after the gap until the gap is resolved or manually handled.
- Because the backend computes contiguity from the stored minimum, the node should ensure the first local sequence is uploaded before considering later acknowledgements sufficient.

### Durable Upload Failure Responses

Known failure cases:

| HTTP | Code | When |
| --- | --- | --- |
| 400 | `MALFORMED_JSON` | Body is not a JSON object or content type prevents JSON parsing |
| 401 | `DEVICE_AUTH_REQUIRED` | Missing/malformed auth headers |
| 401 | `INVALID_DEVICE_CREDENTIALS` | Bad `device_id` or token |
| 403 | `INACTIVE_DEVICE` | Device disabled |
| 413 | `REQUEST_TOO_LARGE` | Whole HTTP request exceeds `MAX_CONTENT_LENGTH` |
| 422 | `VALIDATION_ERROR` | Invalid payload, record, timestamp, number, raw_frame, unknown ECU profile, too many records |
| 500 | `TELEMETRY_STORE_FAILED` | SQLAlchemy storage failure |

Temporary analyzer/ML failures after a completed upload do not roll back telemetry storage and are not supposed to make the upload fail. The session may show `analysis_status=failed` while capture remains stored.

### Idempotency And Duplicate Behavior

Existing idempotency:

- `ride_sessions` has unique `(device_id, session_id)`.
- `telemetry_records` has unique `(device_id, session_id, seq)`.
- Insert uses conflict-do-nothing for PostgreSQL and SQLite.
- Retrying the same records after an ambiguous network failure does not create duplicate telemetry rows.
- Upload acknowledgement is computed from rows actually stored in the database after the insert attempt.

Existing duplicate behavior:

- If the same batch is retried, `inserted_count` may be `0`, `duplicate_count` may equal `received_count`, and `accepted_sequences` still confirms stored data.
- If a request contains duplicate `seq` values, only the first occurrence is attempted for insert; duplicates count against `duplicate_count`.
- If the same `(session_id, seq)` is retried with different content, the backend silently keeps the first stored row and treats later rows as duplicates. It does not compare raw payload hashes.

Not fully idempotent:

- `batch_id` is stored but has no unique constraint.
- Each successful request inserts a `sync_batches` row.
- Each successful request with raw frames can insert a `raw_artifacts` metadata row, including retries.
- There is no upload-level content hash/idempotency key contract.

Smallest compatible backend improvement if full upload-level idempotency becomes required: make `(device_id, ride_session_id, batch_id)` unique when `batch_id` is present, or add a request/content hash with a unique constraint. This is not currently implemented.

## Session Semantics

The server does not have explicit session start or end endpoints.

How a session is created:

- First successful durable upload for `(device, session_id)` creates a `ride_sessions` row.
- `started_at` is `session_started_at` if supplied, otherwise the earliest event time in the uploaded records.
- `last_record_at` is latest event time among stored records.
- `ended_at` is set to latest event time when an upload has `session_ended: true`.

How a session is updated:

- Additional batches with the same `session_id` add records by `seq`.
- `record_count`, `first_seq`, `last_seq`, raw/valid/invalid/checksum counts, and status fields are recomputed from stored rows.
- `sample_interval_ms` and `sampling_rate_hz` are set from the first non-null upload metadata if not already stored.
- `decoder_version_id` is set from the selected/current ECU profile decoder.
- `device.last_seen_at` updates on durable uploads and `/api/telemetry`.

Capture/sync statuses:

| Condition | `sync_status` | `capture_status` |
| --- | --- | --- |
| No `ended_at` yet | `syncing` | `capturing` |
| `ended_at` set and contiguous ack reaches `last_seq` | `complete` | `completed` |
| `ended_at` set but sequence gaps remain | `partial` | `completed_with_gaps` |

`RideSession.vehicle_id` exists in the schema, but current upload code does not set it. Current session ownership/display flows use `Device.vehicle_name`.

## Single Development Record Endpoint

`POST /api/telemetry` accepts the same record fields as one durable record at the top level. It wraps the single record into the same storage path.

```http
POST /api/telemetry
Content-Type: application/json
X-Device-ID: <device_id>
Authorization: Bearer <device_token>
```

Example:

```json
{
  "session_id": "dev-test-001",
  "seq": 0,
  "device_time_ms": 0,
  "raw_frame": "FFFFFFFFFF0218711700001900FFFF81495C597D0000587C0000000077"
}
```

Success status:

- `201` if at least one telemetry row was inserted.
- `200` if the record was a duplicate.

For the node repository, use `/api/logs/upload` for completed/offline sessions. Treat `/api/telemetry` as a development convenience unless the server contract changes.

## Live Preview Contract

Live Preview is an optional near-realtime bench/diagnostic path. It is not durable session upload.

Device endpoint:

```http
POST /api/telemetry/live
Content-Type: application/json
X-Device-ID: <device_id>
Authorization: Bearer <device_token>
```

Required live fields:

| Field | Type |
| --- | --- |
| `session_id` | string max 100 |
| `seq` | integer 0..2^63-1 |
| `device_time_ms` | integer 0..2^63-1 |

Optional live fields:

| Field | Type/constraints |
| --- | --- |
| `timestamp` | Timezone-aware ISO 8601 or Unix epoch seconds |
| `rpm` | Number 0..100000 |
| `tps` | Number 0..100 |
| `tps_voltage` | Number 0..10 |
| `tps_raw_candidate` | Number 0..255 |
| `ect` | Number -80..300 |
| `iat` | Number -80..300 |
| `battery` | Number 0..100 |
| `battery_voltage` | Number 0..100 |
| `iat_c` | Number -80..300 |
| `ect_c_candidate` | Number -80..300 |
| `map_raw` | Number 0..255 |
| `injector_ms` | Number 0..1000 |
| `ignition_deg` | Number -180..180 |
| `injector_raw` | Integer 0..2^31-1 |
| `raw_length` | Integer 0..4096 |
| `fuel_cut_inferred` | Boolean |
| `parser_version` | String max 64 |
| `ecu_profile_id` | String max 100 |
| `raw_frame` | Compact even-length hex string, max `LIVE_RAW_FRAME_MAX_CHARS` chars |

Live Preview rejects unknown fields. Do not send `device_id` in the live JSON body; the server takes it from `X-Device-ID`.

Default live limits:

- `LIVE_SAMPLE_TTL_SECONDS=15`
- `LIVE_RATE_LIMIT_PER_SECOND=5`
- `LIVE_RATE_LIMIT_BURST=10`
- `LIVE_MAX_REQUEST_BYTES=4096`
- `LIVE_RAW_FRAME_MAX_CHARS=256`

Live response:

```json
{
  "ok": true,
  "device_id": "ecu-abc123",
  "server_received_at": "2026-08-26T12:00:05.123Z"
}
```

Live failure cases:

| HTTP | Code | When |
| --- | --- | --- |
| 400 | `MALFORMED_JSON` | Body is not a JSON object |
| 401 | `DEVICE_AUTH_REQUIRED` or `INVALID_DEVICE_CREDENTIALS` | Auth failure |
| 403 | `INACTIVE_DEVICE` | Device disabled |
| 413 | `REQUEST_TOO_LARGE` | Live body too large |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | `Content-Type` is not JSON |
| 422 | `VALIDATION_ERROR` | Invalid field, unknown field, bad raw hex/profile |
| 429 | `LIVE_RATE_LIMITED` | Token-bucket limit exceeded; response includes `Retry-After` |
| 503 | `LIVE_PREVIEW_UNAVAILABLE` | Redis/live cache unavailable |

Live storage behavior:

- Stores only the latest live sample in Redis or process memory.
- Publishes to browser SSE subscribers.
- Does not update `device.last_seen_at`.
- Does not create `RideSession`, `TelemetryRecord`, or `SyncBatch`.
- Does not return `accepted_sequences`.

Browser live endpoints:

```text
GET /devices/<device_id>/live
GET /api/devices/<device_id>/live/latest
GET /api/devices/<device_id>/live/stream
```

These require dashboard login and device ownership/admin access. The stream is Server-Sent Events with `telemetry` events and heartbeat comments.

## Remote Live Mode Control Contract

Remote Live Mode control is separate from Live Preview telemetry. It lets the browser request that the node enable or disable its temporary live telemetry producer. The transport is HTTP polling from the node; no MQTT or WebSocket path is implemented for this feature.

Runtime identity:

- The node must generate a fresh `boot_id` on every boot.
- The node must start each boot with Live Mode off.
- The node sends `boot_id` and current `live_mode` on every control poll.
- Server commands are targeted to exactly one `(device_id, boot_id)`.
- The node must execute a command only when `command.boot_id == current_boot_id`.
- A command for boot A is never returned to boot B.
- A new boot poll resets the server's current runtime view to `reported_live_mode=false` for that new boot and marks old active commands stale.

Device poll endpoint:

```http
POST /api/device/control/poll
Content-Type: application/json
X-Device-ID: <device_id>
Authorization: Bearer <device_token>
```

Poll request:

```json
{
  "boot_id": "boot_123",
  "live_mode": false,
  "firmware_version": "1.0.0",
  "uptime_ms": 2500
}
```

Required fields are `boot_id` and `live_mode`. Optional fields are `firmware_version` and `uptime_ms`. Unknown fields are rejected.

No-command response:

```json
{
  "ok": true,
  "command": null,
  "server_time": "2026-08-26T12:00:00Z"
}
```

Command response:

```json
{
  "ok": true,
  "command": {
    "command_id": "cmd_abc123",
    "boot_id": "boot_123",
    "type": "ENABLE_LIVE_MODE"
  },
  "server_time": "2026-08-26T12:00:00Z"
}
```

Device acknowledgement endpoint:

```http
POST /api/device/control/ack
Content-Type: application/json
X-Device-ID: <device_id>
Authorization: Bearer <device_token>
```

Ack request:

```json
{
  "boot_id": "boot_123",
  "command_id": "cmd_abc123",
  "status": "applied",
  "live_mode": true
}
```

Ack success response:

```json
{
  "ok": true,
  "command_id": "cmd_abc123",
  "status": "applied",
  "live_mode": true,
  "server_time": "2026-08-26T12:00:01Z"
}
```

Supported command types:

- `ENABLE_LIVE_MODE`
- `DISABLE_LIVE_MODE`

Supported acknowledgement statuses:

- `applied`
- `rejected`
- `failed`

Command lifecycle:

- Browser enable/disable creates a command only for the current fresh boot runtime.
- A poll delivers one pending command and marks it `delivered`.
- The same delivered command may be redelivered to the same `boot_id` until acknowledged.
- Ack marks the command `applied`, `rejected`, or `failed`.
- Repeated ack for the same command and same boot is safe.
- Ack for an old boot after a new boot is rejected with `STALE_BOOT_ID` and does not update current live state.
- Duplicate browser clicks for the same boot and command type coalesce to the existing pending/delivered command.
- If Enable is pending and Disable is requested for the same boot, the older command is superseded and the newest command is delivered.

Browser control endpoints:

```text
GET  /api/devices/<device_id>/live/control
POST /api/devices/<device_id>/live/enable
POST /api/devices/<device_id>/live/disable
```

Browser routes require login, ownership/admin access, and CSRF protection for POST. They never expose the device token.

Control availability:

- The server tracks control availability from `last_control_poll_at`, not from durable `Device.last_seen_at`.
- Default freshness threshold is `CONTROL_POLL_STALE_SECONDS=10`.
- If no runtime exists or the last poll is stale, browser enable/disable returns `409 DEVICE_OFFLINE` and no indefinite command is queued.
- Offline/stale status reports `reported_live_mode=false` to avoid presenting stale ON state.

Control validation errors use the standard API error shape:

```json
{
  "ok": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "boot_id is required"
  }
}
```

## Web/Dashboard Display Contract

Durable session displays use stored canonical fields from `TelemetryRecord.to_dict()` and analysis payloads from `services.analysis`.

Displayed/available durable signals include:

- `rpm`
- `tps_voltage`
- `tps_raw_candidate`
- `battery_voltage`
- `iat_c`
- `ect_c_candidate`
- `map_raw`
- `tps`
- `ect`
- `iat`
- `battery`
- `injector_ms`
- `ignition_deg`
- anomaly score fields computed by server analysis or embedded metadata
- raw frame and parsed raw metadata in the Tool page

Live Preview displays:

- `rpm`
- `tps`
- `tps_voltage`
- `tps_raw_candidate`
- `ect_c_candidate` with fallback to `ect`
- `iat_c` with fallback to `iat`
- `map_raw`
- `battery_voltage` with fallback to `battery`
- `injector_ms`
- `fuel_cut_inferred`
- `session_id`
- `seq`
- `device_time_ms`
- `server_received_at`
- `decoder_id:decoder_version` or fallback `parser_version`
- `ecu_profile_id`
- `frame_valid`
- `checksum_valid`
- `injector_raw`
- `raw_length`
- `raw_frame`

Frontend code formats units and precision. It does not perform authoritative ECU byte decoding. Legacy `static/js/liveData.js`, `static/js/settings.js`, and `static/js/webSocket.js` reference a `ws://<host>:5000/ws` style transport, but the current Flask app has no `/ws` route and no WebSocket server registration.

## Device Status

Current server-derived device status:

- `Device.is_active` controls whether ingest requests are allowed.
- `Device.last_seen_at` updates on durable uploads and `/api/telemetry`, not Live Preview.
- Dashboard "Reader state" is `"Offline"` if `last_seen_at` is null, otherwise `"Synced"`.
- Pending uploads in the dashboard are counted as backend sessions with `sync_status="syncing"`. This is not a device-reported queue depth.
- Dashboard error state is `"Inactive"` only when the device is disabled, otherwise `"--"`.
- Live online/offline status is derived separately from the latest live sample TTL.
- Live Mode control availability is derived from `DeviceControlRuntime.last_control_poll_at` and `CONTROL_POLL_STALE_SECONDS`.
- Live Mode ON/OFF state is runtime state reported by the node for its current `boot_id`; stale/offline runtimes are reported as unavailable/off.

Missing current device-status contract:

- No `POST /api/device/status`.
- No backend field for node storage free space, Wi-Fi status, ECU link status, reset reason, local queue depth, or firmware error state.
- No general-purpose device status polling response beyond Live Mode control polling.

## Commands, Polling, MQTT, WebSocket, And Wi-Fi Configuration

Currently implemented for the node:

- Live Mode command polling at `POST /api/device/control/poll`.
- Live Mode command acknowledgement at `POST /api/device/control/ack`.

Not currently implemented:

- Server-to-device commands beyond `ENABLE_LIVE_MODE` and `DISABLE_LIVE_MODE`.
- Device-side configuration download endpoint.
- Wi-Fi SSID/password/static IP provisioning from server.
- Firmware update or filesystem update endpoint.
- MQTT worker or broker authentication for telemetry.
- WebSocket telemetry ingest.

`mqtt_service.py` contains only placeholder topic constants:

```text
ecu/{device_id}/telemetry
ecu/{device_id}/status
ecu/{device_id}/logs
```

`docker-compose.yml` includes an optional Mosquitto service profile, and `deploy/mosquitto.conf` is a development placeholder. No application code consumes MQTT messages today.

The current active Settings page stores dashboard display preferences only. Older static settings code references `/wifiOptions`, `/protocolOptions`, `/firmwareUpdate`, `/fileSystemUpdate`, and `/pidSelect`, but those routes are not registered by the Flask app.

## Node Implementation Checklist For The Separate Repo

Implement against the existing contract:

- Provide a setup path where the user enters backend base URL, pairing code, node-generated `device_id`, `device_name`, `vehicle_name`, `ecu_type`, `firmware_version`, and `hardware_version`.
- Call `POST /api/device/pair` once and persist `device_id` plus `device_token` securely.
- Generate a stable `session_id` for each ride/logging session.
- Assign monotonic non-negative `seq` values per session and never reuse one for different samples.
- Store RAW ECU frames locally before attempting upload.
- Upload durable data with `POST /api/logs/upload` JSON batches, not JSONL/files directly.
- Prefer compact uppercase `raw_frame` hex for the 29-byte `honda_keihin_legacy_29` frame.
- Include `device_time_ms`; include timezone-aware `timestamp` only if the node has a reliable wall clock.
- Send `session_ended: true` only for the final batch of a session.
- Retry exactly the same `(session_id, seq, raw_frame)` records after network ambiguity.
- Delete local records only through the durable acknowledgement rule using `accepted_sequences.contiguous_until`.
- Generate a fresh `boot_id` every boot and start Live Mode off.
- Poll `POST /api/device/control/poll` every 1-3 seconds while online.
- Execute only Live Mode commands whose `boot_id` matches the current boot.
- Acknowledge commands at `POST /api/device/control/ack` after applying, rejecting, or failing.
- Use Live Preview only as optional diagnostics and obey `429 Retry-After`.

Do not assume the backend supports:

- JSONL upload as an HTTP content type.
- Multipart session file upload.
- Server commands beyond runtime Live Mode enable/disable.
- Token refresh.
- Wi-Fi/device config download.
- MQTT/WebSocket ingestion.
- Vehicle database id assignment during pairing.
- Batch-level idempotency by `batch_id`.
