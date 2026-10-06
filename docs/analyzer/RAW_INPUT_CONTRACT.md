# RAW Input Contract

This contract describes input formats accepted by this repository today.

## Public Input Surfaces

| Surface | Input | Notes |
| --- | --- | --- |
| `POST /api/v1/analysis` | JSON `CanonicalTelemetrySession` | Preferred path. This is not RAW ingestion. |
| `POST /api/v1/sessions` | Multipart file plus form metadata | RAW compatibility path. |
| `POST /api/v1/import/raw-session` | Multipart file plus form metadata | Same processor as `/sessions`, explicitly named for raw import. |
| `import_raw_text(text, settings, **metadata)` | Legacy text content | Python API. |
| `import_raw_file(path, settings, **metadata)` | Legacy text or JSONL file | Python API with file auto-detection. |
| `parse_log_file(path)` | Legacy text or JSONL file | Parser-level API. |

`parse_log_file` treats a file as JSONL when either:

- the extension is `.jsonl`, or
- the first non-blank line starts with `{`.

Otherwise it uses legacy text parsing.

## RAW Upload Metadata

The RAW HTTP endpoints accept these form fields:

| Field | Type | Required | Validation |
| --- | --- | --- | --- |
| `file` | upload file | yes | non-empty; size <= `MAX_UPLOAD_MB` (default 25). |
| `device_id` | string | no, except with client `session_id` | Required when client `session_id` is supplied. |
| `vehicle_id` | string | no | Stored as metadata. |
| `sample_interval_ms` | float | no | Must be positive if supplied. |
| `sampling_rate_hz` | float | no | Must be positive if supplied. |
| `session_note` | string | no | Stored as metadata. |
| `firmware_version` | string | no | Stored as metadata. |
| `session_id` | string | no | Client idempotency key; regex `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`. |
| `process_with_model` | bool | no | Default `true`; when false, feature extraction runs but inference is skipped. |

When `device_id` and client `session_id` are supplied, uploads are idempotent by
SHA-256. The same payload reuses the existing result. A different payload for
the same `(device_id, session_id)` returns HTTP 409.

## Legacy Text RAW Format

Parser: `app.ingestion.raw_ecu.parser.parse_log_text`

Accepted frame markers:

```text
RAW: FF;FF;FF;FF;FF;02;18;71;17;...
```

or:

```text
RAW:
FF;FF;FF;FF;FF;02;18;71;17;...
```

Rules:

- Marker is case-insensitive and must match `RAW:`.
- Non-RAW lines are ignored.
- If `RAW:` has an empty payload, the next non-blank line is used as payload.
- Payload tokens are split by semicolon.
- Tokens must be 1 or 2 hexadecimal characters.
- Exactly 29 bytes are required.
- One-character tokens such as `2` are accepted and decoded as `0x02`.
- Frame index is the zero-based order of discovered RAW frames.
- Legacy text does not carry per-frame timestamps.

Malformed legacy frames are preserved in `ParseResult.frames` with:

- `parse_ok=false`
- `parse_error=<reason>`
- `checksum_valid=false`

Legacy text uploads are rejected only when no RAW frames are found. If malformed
legacy frames exist, the pipeline may still proceed, but ML validation later
requires enough eligible samples.

## ESP JSONL RAW Format

Parser: `app.ingestion.raw_ecu.parser.parse_jsonl_file`

Each non-blank line must be a JSON object. Required payload field:

| Field | Type | Required | Semantics |
| --- | --- | --- | --- |
| `raw_hex` | string | yes | ECU frame bytes as compact hex or separated hex tokens. |

Optional fields used by parser:

| Field | Type | Semantics |
| --- | --- | --- |
| `elapsed_ms` | numeric or numeric string | First-priority timestamp. |
| `timestamp_ms` | numeric or numeric string | Second-priority timestamp. |
| `device_time_ms` | numeric or numeric string | Third-priority timestamp. |
| `raw_length` | numeric or numeric string | If present, must equal parsed `raw_hex` byte count. |

Timestamp field priority is:

```text
elapsed_ms -> timestamp_ms -> device_time_ms
```

`raw_hex` accepted encodings:

- compact hex: `0218711703151900...`
- optional leading `0x` or `0X` for compact strings
- separated tokens using whitespace, semicolon, comma, tab, or newline
- separated tokens may individually use `0x`/`0X`

Frame-length rules:

| Parsed length | Accepted? | Conversion |
| --- | --- | --- |
| 24 bytes | yes | Prepends five `FF` bytes for legacy 29-byte compatibility. |
| 29 bytes with first five bytes `FF FF FF FF FF` | yes | Used as-is. |
| 29 bytes without five-`FF` prefix | no | Parse error. |
| Other length | no | Parse error. |

Malformed JSONL uploads are rejected before committing raw files or session
entities. The error reports up to the first three failed lines.

Examples of rejected JSONL conditions:

- malformed JSON
- line is not a JSON object
- missing/non-string/empty `raw_hex`
- non-hex `raw_hex`
- odd-length compact hex
- invalid 24/29-byte length
- `raw_length` mismatch
- non-numeric timestamp or `raw_length`

## Checksum Rules

### Current Runtime Legacy 29-Byte Compatibility

Module: `app.ingestion.raw_ecu.checksum`

```python
len(frame) == 29
sum(frame) % 256 == 251
expected_checksum = (251 - sum(frame[:28])) % 256
actual_checksum = frame[28]
```

Checksum-invalid frames are decoded into rows only when the frame parses, but
they are not ML-eligible. Default window settings reject any window with checksum
failure ratio above `0.0`.

### Audited 24-Byte Honda/Keihin `0x71/0x17` Profile

Module: `app.ecu.profiles.honda_keihin_71_17`

```python
len(frame) == 24
frame[:4] == [0x02, 0x18, 0x71, 0x17]
sum(frame) % 256 == 0
expected_checksum = (-sum(frame[:23])) & 0xFF
```

This profile can normalize a 29-byte five-`FF`-prefixed frame to 24 bytes when
`allow_legacy_29_byte=True`. This is used by audit/profile training code and
tests; it is distinct from the current runtime RAW compatibility adapter.

## Time Semantics

The RAW adapter stores canonical `timestamp_ms` as:

1. the parsed JSONL timestamp if present, else
2. `frame_index * sample_interval_ms`, else
3. `frame_index * (1000 / sampling_rate_hz)`, else
4. `None` with sample-index time basis.

RAW session result `input.time_basis` is:

- `source_timestamp_ms` when JSONL supplied timestamps,
- `relative_time_ms` when `sample_interval_ms` is supplied,
- `sampling_rate_hz` when only sampling rate is supplied,
- `sample_index` otherwise.

## Parser Statistics

Both parsers return:

- `total_lines`
- `frames_found`
- `frames_parsed`
- `frame_errors`
- `checksum_valid_frames`
- `checksum_invalid_frames`
- `source_format`
- `timestamped_frames`

`source_format` is one of:

- `legacy_raw_text`
- `esp_jsonl`

## Explicit Non-Contracts

This repository does not prove support for:

- binary uploads,
- arrays of JSON objects,
- CAN frames,
- arbitrary OBD-II PID payloads,
- live UART/K-Line streams,
- Cloud-authenticated session envelopes,
- ESP firmware messages beyond the JSONL fields above.
