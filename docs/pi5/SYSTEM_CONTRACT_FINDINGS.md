# System Contract Findings

## Authority Statement

Cloud owns no ECU semantic decoding.
Analyzer is the sole ECU decode authority.
RAW-only durable sessions are sent to Analyzer.
Already-decoded/analyzed Pi sessions are persisted directly.

Comparison scope:

- ESP contract from `docs/task/*`.
- Cloud implementation in this repository.
- Analyzer contract from `docs/task/*`.

Classification values:

- `MATCH`
- `REPRESENTATION ADAPTER`
- `SEMANTIC MISMATCH`
- `CONTRACT MISMATCH`
- `UNKNOWN`

| Boundary | ESP contract | Cloud implementation | Analyzer contract | Classification | Finding |
| --- | --- | --- | --- | --- | --- |
| Production raw field | Durable upload sends `raw_frame`. | `/api/logs/upload` accepts `raw_frame`, persists `raw_hex`. | RAW endpoint accepts `raw_hex`. | MATCH | Cloud maps transport RAW to Analyzer RAW without semantic decode. |
| 24 vs 29 bytes | ESP native/storage is 24 bytes; durable upload is 29 bytes. | Persists `raw_representation=legacy29_ff5`. | Analyzer strips FFx5 and decodes native24. | MATCH | Representation boundary is explicit. |
| Five `FF` prefix | Generated compatibility padding, not ECU wire bytes. | Preserved as RAW transport only. | Analyzer handles the prefix. | MATCH | Cloud no longer treats FF padding as semantic ECU bytes. |
| Checksum | 24-byte sum-zero; 29-byte compatibility sum `% 256 == 251`. | Cloud does not validate Honda checksum semantically. | Analyzer validates Honda/native frame rules. | MATCH | Analyzer is the checksum/decode authority. |
| Header/profile | ESP native active frame starts `02 18 71 17`. | Cloud stores RAW only. | Analyzer enforces native header/profile. | MATCH | Header identity is protected by Analyzer. |
| `ecu_profile_id` | ESP sends `honda_keihin_legacy_29`; Pi sends `honda_keihin_71_17`. | ESP profile is `transport_profile_id`; semantic provenance is Analyzer/Pi V2. | Analyzer authoritative profile is `honda_keihin_71_17:1.0.0`. | MATCH | Legacy ESP profile no longer means Cloud semantic decoder. |
| `session_id` | `<device_id>-raw-%06u`, reused on retry. | Unique per DB device plus `session_id`. | Analyzer raw HTTP has optional client session id; canonical accepts session id metadata. | MATCH | Cloud identity scope is authenticated device plus session id. |
| `seq` | Zero-based session-local sequence, reused on retry. | Non-negative integer; unique per device/session/seq. | Canonical sample sequence must be unique/monotonic for ML. | MATCH | Cloud stores by sequence and canonical builder orders by seq. |
| Duplicate retry | ESP expects duplicate handling. | Duplicate inserts ignored and ACK still reports stored range. | Analyzer idempotency is separate for raw uploads. | MATCH with caveat | Current Cloud lacks conflict detection for different duplicate content. |
| `device_time_ms` | Session-relative elapsed ms in durable upload. | Stored as integer; canonical `timestamp_ms` falls back to it. Session start/end fall back to server receive time if no wall-clock timestamp. | Canonical `timestamp_ms` is session-local/source-relative. | SEMANTIC MISMATCH | Sample axis matches; session wall-clock/duration can be misleading. |
| Analyzer invocation | ESP only uploads to Cloud. | Cloud calls Analyzer HTTP `/api/v1/raw/decode-analyze` with stored RAW after final upload. | Analyzer RAW endpoint returns canonical V2 + analysis. | MATCH | One finalized ESP session produces one RAW Analyzer call. |
| Analysis result | ESP does not produce analysis. | Cloud stores Analyzer summary only. | Analyzer can output summary and persist richer local artifacts. | REPRESENTATION ADAPTER | Cloud stores a subset of Analyzer output. |
| Decoder provenance | ESP sends transport profile; firmware/hardware at pairing. | Cloud stores transport metadata and Analyzer-returned semantic provenance. | Canonical session includes `ecu_profile_id`, `decoder_id`, `decoder_version`. | MATCH | Semantic provenance is Analyzer-owned. |
| Model provenance | ESP none. | Cloud stores `model_version`, `feature_schema_version`, summary JSON. | Analyzer summary includes model and feature schema fields. | REPRESENTATION ADAPTER | Enough for basic display; not full model artifact provenance. |
| Live telemetry | ESP sends disposable live sample with optional raw frame. | `/api/telemetry/live` caches supplied fields and RAW diagnostic data only. | Analyzer not involved. | MATCH | Live Mode does not decode RAW. |
| Pi V2 telemetry sync | Pi emits RAW24 + canonical native V2 records. | `/api/device/sync/session` accepts RAW24 and canonical V2 records. | Analyzer Pi contract uses `rpm`, `tps_voltage`, `tps_raw`, `battery_voltage`, `iat_c`, `ect_c`. | MATCH | Dedicated Pi path stores RAW and V2 without Analyzer calls. |
| Pi sync idempotency | Pi retries with stable `session_id` and `seq`. | Pi path fingerprints normalized RAW + semantic content and returns 409 on conflicts. | Analyzer idempotency is separate from Cloud record storage. | MATCH | ESP duplicate behavior remains unchanged. |
| Pi local analysis | Pi should analyze offline and sync later. | Final Pi sync imports `AnalysisResult` with provenance and does not call Cloud Analyzer. | Analyzer can run locally on Pi in product target. | MATCH | Cloud displays Pi-generated analysis summary through existing APIs. |
| Pi time semantics | Pi `timestamp_ms` is relative elapsed time. | Pi path computes `duration_ms` from stored relative timestamps and keeps wall-clock capture metadata separate. | Canonical telemetry treats `timestamp_ms` as source/session relative. | MATCH | Cloud no longer infers Pi ride duration from upload receive span. |

## Bottom Line

The ESP durable path remains compatible because ESP still sends the same
29-byte `honda_keihin_legacy_29` transport representation. Cloud stores it as
RAW and routes completed sessions through Analyzer for authoritative decode.

The Pi sync semantics gap is closed for the MVP by
`/api/device/sync/session`: Cloud accepts completed or batched canonical V2
sessions plus native RAW24, preserves Pi decoder/model provenance, imports
Pi-generated analysis, and enforces idempotent conflict handling without
changing ESP firmware behavior.

Cloud owns no ECU semantic decoding. Analyzer is the sole ECU decode authority.
