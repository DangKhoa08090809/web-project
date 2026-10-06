# ESP Ingestion Flow

## Authority Statement

Cloud owns no ECU semantic decoding.
Analyzer is the sole ECU decode authority.
RAW-only durable sessions are sent to Analyzer.
Already-decoded/analyzed Pi sessions are persisted directly.

## Pairing: `POST /api/device/pair`

HTTP handler: `routes/ingest.py:56`.

1. Request body must be a JSON object.
2. `services.provisioning.pair_device` normalizes and hashes `pairing_code`.
3. The matching `DevicePairingCode` is loaded with `with_for_update()`.
4. Cloud rejects missing, used, expired, over-attempted, or disabled-owner
   pairing codes.
5. Device metadata is validated: `device_id`, `device_name`, `vehicle_name`,
   `ecu_type`, optional `firmware_version`, optional `hardware_version`.
6. Existing `device_id` is rejected.
7. Cloud generates `secrets.token_urlsafe(32)`, stores only its SHA-256 hash,
   creates `Device(user_id=<pairing-code owner>)`, marks the pairing code used,
   and returns the token once.

Data relationship after pairing:

```text
User
  -> Device
```

No `Vehicle` row is created. `Device.vehicle_name` is a string used by session
display and canonical metadata.

## Durable Upload: `POST /api/logs/upload`

HTTP handler: `routes/ingest.py:142`.

Authentication:

- Requires `X-Device-ID` and `Authorization: Bearer <device_token>`.
- Looks up `Device.device_id`, verifies token hash, rejects inactive devices.

Request validation:

- Top-level `session_id`: required string, max 100 chars.
- Top-level `records`: non-empty array, max `MAX_LOG_RECORDS`.
- Top-level `session_ended`: boolean, default false.
- Top-level `batch_id`: optional string, max 100 chars.
- Top-level `session_started_at`: optional timezone-aware ISO string or epoch.
- Top-level `ecu_profile_id`: optional string, defaults later to
  `honda_keihin_legacy_29`.
- Record `seq`: required non-negative integer.
- Record `device_time_ms`: optional non-negative integer.
- Record `timestamp`: optional wall-clock timestamp.
- Record `timestamp_ms`: optional non-negative float.
- Record `raw_frame`: optional hex string or object containing `hex`,
  `raw_hex`, or `frame_hex`.

Unknown top-level and record keys are ignored, except `raw_frame` object
metadata is preserved if JSON-serializable.

## Processing Steps

```text
request JSON
  -> device_required auth
  -> validate_batch
  -> store_upload
  -> insert RideSession if absent
  -> insert RAW-only TelemetryRecord rows with ON CONFLICT DO NOTHING
  -> compute ACK from stored sequence set
  -> refresh session metadata/status
  -> insert SyncBatch
  -> insert RawArtifact metadata if raw_frame exists
  -> commit
  -> optional synchronous Analyzer RAW HTTP submission after final batch
  -> ACK JSON
```

## RAW And Decode Behavior

- `raw_frame` is preserved in `TelemetryRecord.raw_frame`.
- `raw_hex`, `raw_length`, and `raw_representation = legacy29_ff5` are stored
  for Analyzer transport.
- Cloud does not semantically decode `raw_frame` and does not know Honda signal
  byte offsets.
- `ecu_profile_id = honda_keihin_legacy_29` is preserved as
  `transport_profile_id`; it is not semantic decoder provenance.
- On a completed session, Cloud sends stored RAW to Analyzer
  `/api/v1/raw/decode-analyze`.
- Analyzer strips `FF x5`, decodes native24 with
  `honda_keihin_71_17:1.0.0`, returns canonical V2 and analysis, and Cloud
  persists those fields.

## Persistence

`RideSession`:

- Unique by `(device_id, session_id)`.
- Stores start/end/last record times, sample metadata, RAW representation,
  transport profile, Analyzer semantic provenance after decode, counts,
  sequence range, sync/capture/analysis statuses, latest `analysis_run_id`.

`TelemetryRecord`:

- Unique by `(device_id, session_id, seq)`.
- Stores device time, optional wall-clock timestamp, RAW frame JSON/string,
  explicit raw representation fields, and decoded canonical V2 fields only
  after Analyzer returns them.

`SyncBatch`:

- Stores received/inserted/duplicate counts and sequence range.
- `batch_id` is stored but not unique.

`RawArtifact`:

- Stores a hash and logical DB path for raw frames from the accepted batch.
- It is not a file containing Analyzer JSONL.

## ACK Generation

Response shape:

```json
{
  "ok": true,
  "device_id": "...",
  "session_id": "...",
  "received_count": 100,
  "inserted_count": 100,
  "duplicate_count": 0,
  "accepted_sequences": {
    "minimum": 0,
    "maximum": 99,
    "contiguous_until": 99
  },
  "server_time": "..."
}
```

`accepted_sequences` is computed from all stored sequences for the
authenticated device and session. The contiguous range starts at the lowest
stored sequence, not explicitly at zero. ESP currently starts at zero, so this
matches the production contract for normal ESP uploads.

## Retry And Idempotency

Current behavior:

- Same `(device, session_id, seq)` retried after storage is a duplicate and
  returns success.
- Overlapping batches are accepted.
- Same `batch_id` can be stored more than once.
- A duplicate `seq` with different content is silently ignored because the
  insert conflict does nothing. There is no explicit conflict response or
  content hash comparison.

This is adequate for current ESP retry behavior but is a gap for Pi sync
semantics where "same identity + different content" should be explicit.
