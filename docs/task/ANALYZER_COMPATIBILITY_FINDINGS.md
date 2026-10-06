# Analyzer Compatibility Findings

Reference docs compared:

- `ANALYZER_HANDOFF.md`
- `HONDA_KEIHIN_DECODER.md`
- `RAW_INPUT_CONTRACT.md`

ESP firmware remains the source of truth for ESP behavior.

## Findings Table

| Topic | Analyzer reference | ESP finding | Classification |
| --- | --- | --- | --- |
| 24 vs 29 byte representation | Analyzer supports 24-byte audited profile and 29-byte legacy runtime | ESP wire/internal/storage frame is 24 bytes; durable upload is 29 bytes after compatibility conversion | REPRESENTATION DIFFERENCE |
| FF prefix origin | Analyzer accepts 29-byte frames only with five leading `FF`; docs do not prove ESP origin | ESP adds five `FF` bytes in `node_table17_to_backend_raw`; UART reader does not store them | MATCH |
| Header `02 18 71 17` | Audited profile requires header; legacy runtime decoder does not enforce it | ESP active logging validates this header at node offsets 0..3 | MATCH |
| Checksum rule | 24-byte audited profile: `sum % 256 == 0`; 29-byte legacy: `sum % 256 == 251` | ESP enforces 24-byte sum-zero on node frames and 29-byte sum-251 after adding five `FF` bytes | MATCH |
| `raw_hex` format | Analyzer JSONL accepts `raw_hex`, compact or separated, 24 or 29 bytes | ESP diagnostic JSONL emits uppercase compact `raw_hex`; production Cloud upload uses `raw_frame`, not `raw_hex` | CONTRACT MISMATCH |
| Timestamp fields | Analyzer JSONL priority: `elapsed_ms -> timestamp_ms -> device_time_ms` | ESP diagnostic JSONL emits `device_time_ms`; production upload uses `device_time_ms` but value is session-relative `elapsed_ms`; binary storage field is `elapsed_ms` | REPRESENTATION DIFFERENCE |
| `raw_length` | Analyzer JSONL optional length must match parsed frame | ESP diagnostic JSONL includes `raw_length`; durable `/api/logs/upload` omits `raw_length`; live optional raw upload uses `raw_length` 29 when `raw_frame` is included | REPRESENTATION DIFFERENCE |
| Session IDs | Analyzer raw HTTP endpoint accepts optional client `session_id` as idempotency key | ESP durable session id is `<device_id>-raw-%06u`; batch id is `<session_id>-%04llu`; ACK validates matching `device_id` and `session_id` | NOT ENOUGH EVIDENCE |
| ECU profile id | Analyzer legacy runtime uses `honda_keihin_legacy_29`; audited profile uses `honda_keihin_71_17...` | ESP durable and live uploads send `honda_keihin_legacy_29` even though internal frame is 24-byte Table 0x17 | REPRESENTATION DIFFERENCE |
| Local dump format | Analyzer accepts legacy text and JSONL raw files | ESP local `/api/logs/dump` streams binary `ECUR` raw sessions, not Analyzer JSONL | CONTRACT MISMATCH |

## CONTRACT MISMATCH Details

### Production Upload Is Not Analyzer JSONL

Analyzer `RAW_INPUT_CONTRACT.md` describes ESP JSONL with `raw_hex`. The active ESP production Cloud upload sends:

```json
{
  "records": [
    {
      "seq": 0,
      "device_time_ms": 0,
      "raw_frame": "FFFFFFFFFF..."
    }
  ]
}
```

It does not use `raw_hex` in the production durable upload path.

### Local Dump Is Binary, Not JSONL

Analyzer raw import accepts legacy text or JSONL. ESP local diagnostics route:

```text
GET /api/logs/dump?file=session_000123.ecu
```

streams `application/octet-stream` bytes with:

```text
ECUR header + 28-byte raw records
```

It does not expand binary sessions to JSONL.

## Compatibility Summary

The Analyzer's 24-byte audited Honda/Keihin profile matches the ESP wire/internal frame contract. The Analyzer's 29-byte legacy runtime matches the ESP Cloud compatibility representation, not the ESP UART/storage representation.

The safe bridge statement is:

```text
ESP node frame = Analyzer audited 24-byte profile.
ESP durable upload frame = Analyzer legacy 29-byte compatibility profile.
```
