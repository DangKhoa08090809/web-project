# RAW Capture Contract

## Active Local Storage

The active road logger stores binary raw sessions in the `telemetry` data partition, not SPIFFS and not JSONL.

Session file layout:

```text
12-byte SessionFileHeader
N * 28-byte RawTelemetryRecord
```

`SessionFileHeader`:

| Field | Size | Value |
| --- | ---: | --- |
| magic | 4 | `ECUR` |
| version | 1 | `1` |
| frame_size | 1 | `24` |
| record_size | 1 | `28` |
| flags | 1 | `0xFF` active, `0x7F` finalized, `0x00` deleted |
| session_seq | 4 | Little-endian raw session sequence |

`RawTelemetryRecord`:

| Field | Size | Semantics |
| --- | ---: | --- |
| elapsed_ms | 4 | Session-relative timestamp, clamped to uint32 |
| frame | 24 | Validated Honda Table 0x17 node frame |

## Diagnostic JSONL Helper

`raw_capture_jsonl` emits:

```json
{"device_time_ms":1926687,"raw_hex":"02187117","raw_length":4,"checksum_ok":true}
```

Rules:

- `raw_hex` is uppercase compact hex.
- No separators or `0x` prefixes are emitted.
- `raw_length` is the supplied byte length.
- `checksum_ok` is supplied by caller.
- No decoded fields are emitted.
- This helper is tested but is not used by the active road logger main loop.

## Durable Cloud Upload

Production upload does not send `raw_hex`; it sends `raw_frame` inside `/api/logs/upload`.

```json
{
  "session_id": "ecu-ab12ef-raw-000007",
  "session_ended": false,
  "batch_id": "ecu-ab12ef-raw-000007-0000",
  "ecu_profile_id": "honda_keihin_legacy_29",
  "records": [
    {
      "seq": 0,
      "device_time_ms": 0,
      "raw_frame": "FFFFFFFFFF0218711700001900FFFF81495C597D0000587C0000000077"
    }
  ]
}
```

Upload conversion:

```text
stored 24-byte frame
  -> node_table17_to_backend_hex
  -> five FF prefix bytes added
  -> 29-byte uppercase compact hex raw_frame
```

## Local Dump Route

`GET /api/logs/dump?file=session_000123.ecu` streams `application/octet-stream` bytes for the binary raw session. It does not expand the session to JSONL.
