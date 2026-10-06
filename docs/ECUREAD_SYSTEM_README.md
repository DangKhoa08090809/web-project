# EcuRead System README

## Authority Statement

Cloud owns no ECU semantic decoding.
Analyzer is the sole ECU decode authority.
RAW-only durable sessions are sent to Analyzer.
Already-decoded/analyzed Pi sessions are persisted directly.

## What EcuRead Is

EcuRead is the motorcycle ECU telemetry system used by this repo family. It
captures Honda Keihin ECU frames, normalizes them into canonical telemetry,
runs anomaly-oriented analysis, and displays durable ride/session history in
Cloud.

The system has two supported ingestion editions:

- ESP compatibility edition: an ESP reader uploads durable RAW29 logs to
  Cloud. Cloud stores RAW unchanged, then sends completed RAW sessions to
  Analyzer for normalization, ECU decode, canonical V2 construction, and
  analysis.
- Pi5 native edition: a Raspberry Pi 5 sends native RAW24 plus already-decoded
  canonical V2 telemetry and local Analyzer summary to Cloud.

## Repository Map

| Area | Responsibility |
| --- | --- |
| Cloud/Web | Flask app, device pairing, RAW/canonical/session storage, dashboards, browser APIs. |
| ESP node | Existing compatibility reader and live/durable upload client. |
| Analyzer | Canonical telemetry analysis, feature/window model, summary result contract. |
| Pi5 runtime | Native ECU capture/decoder plus local Analyzer execution before Cloud sync. |

This repository owns the Cloud/Web behavior. The Pi5 runtime and Analyzer
implementations remain separate runtime concerns, with contracts documented
under `docs/analyzer`, `docs/esp_node`, and `docs/pi5`.

## Native ECU Contract

The Pi5 native target uses Honda Keihin response frames:

- Native frame length: 24 bytes.
- Header: `02 18 71 17`.
- Checksum: sum-zero over the native frame.
- Canonical provenance:
  - `telemetry_schema_version`: `canonical-telemetry-v2`
  - `ecu_profile_id`: `honda_keihin_71_17`
  - `decoder_id`: `honda_keihin_71_17`
  - `decoder_version`: `1.0.0`

Cloud owns no ECU semantic decoder. Analyzer is the sole ECU decode authority.
Cloud does not reinterpret Pi native RAW frames; Pi sends RAW24, canonical V2,
and analysis to Cloud.

## Canonical Telemetry V1 And V2

Canonical V1 remains readable for historical data:

- `rpm`
- `tps_voltage`
- `tps_raw_candidate`
- `battery_voltage`
- `iat_c`
- `ect_c_candidate`
- `map_raw`

Canonical V2 is the current runtime schema for both Analyzer-decoded ESP RAW
and Pi native sync:

- `rpm`
- `tps_voltage`
- `tps_raw`
- `battery_voltage`
- `iat_c`
- `ect_c`

V2 does not require or fake `map_raw`. Cloud stores native V2 `tps_raw` and
`ect_c`; it does not rename them into the V1 candidate columns.

## Analysis

ESP sessions are submitted by Cloud to Analyzer over HTTP when
`ML_API_BASE_URL` is configured:

```text
stored RAW29
  -> POST /api/v1/raw/decode-analyze
  -> canonical-telemetry-v2 + analysis
  -> Cloud persists canonical fields and AnalysisResult
```

Pi5 sessions are different: Pi is the analysis source. Cloud imports the
Pi-generated analysis summary and persists its provenance:

- `analysis_run_id`
- `model_version`
- `feature_schema_version`
- `telemetry_schema_version`
- `ecu_profile_id`
- `decoder_id`
- `decoder_version`
- `signal_columns`
- `overall_status`

Cloud does not call the Cloud Analyzer for Pi imported sessions. Manual rerun
requests for Pi native sessions return the imported result when present.

Analyzer status is stored as the source of truth for completed analysis. Current
runtime statuses are:

- `ok`: enough evidence and no material anomaly.
- `monitor`: enough evidence with low/moderate anomaly signal.
- `attention`: enough evidence with high anomaly signal.
- `limited_data`: model scoring ran, but the session produced fewer than 10
  feature windows, so Cloud displays a neutral "Limited data" result.
- `no_windows`, `model_unavailable`, `not_scored`: analysis could not produce a
  scored runtime verdict, so Cloud displays "Analysis unavailable".

For `limited_data`, Cloud still preserves `health_score`, `anomaly_ratio`,
`anomaly_window_count`, `window_count`, and optional evidence metadata such as
`evidence_window_count`, `minimum_windows_for_status`, and
`evidence_sufficient` inside the analysis summary.

## Cloud Responsibilities

- Pair devices through `/api/device/pair`.
- Authenticate device sync with `X-Device-ID` plus bearer token.
- Accept ESP durable uploads at `/api/logs/upload` unchanged.
- Accept Pi native session sync at `/api/device/sync/session`.
- Persist RAW, canonical telemetry returned/imported as V2, session lifecycle,
  batch ACKs, analyzer provenance, and analysis summaries.
- Enforce device-scoped session identity and Pi content conflicts.
- Display V1 and V2 telemetry through the existing session/analysis UI.

## Pi Responsibilities

- Capture and validate native 24-byte ECU frames.
- Decode native frames into canonical V2 records or call local Analyzer.
- Maintain session-relative `timestamp_ms`.
- Run the local Analyzer with V2 feature schema `ecu-window-features-v1`.
- Sync completed or partial batches to Cloud with stable `session_id` and `seq`.
- Retry safely using the same semantic record content.

## Compatibility Matrix

| Path | Schema | Decoder | Analysis Source | Cloud Endpoint | Status |
| --- | --- | --- | --- | --- | --- |
| ESP durable upload | RAW29, then `canonical-telemetry-v2` from Analyzer | Analyzer `honda_keihin_71_17:1.0.0` | Analyzer RAW endpoint | `/api/logs/upload` | Supported |
| ESP live preview | Disposable live sample | No Cloud decode | None | `/api/telemetry/live` | Supported |
| Pi5 native sync | RAW24 + `canonical-telemetry-v2` | Pi/Analyzer `honda_keihin_71_17:1.0.0` | Pi local Analyzer | `/api/device/sync/session` | Supported |
| Pi Cloud Live Mode | N/A | N/A | N/A | None | Not part of MVP |

## Source-Of-Truth Rules

- Device identity is the authenticated Cloud device, not a body-provided owner.
- Session identity is `(authenticated device, session_id)`.
- Record identity is `(authenticated device, session_id, seq)`.
- Pi duplicate records must have the same normalized RAW plus semantic
  fingerprint.
- Pi duplicate analysis must have the same `analysis_run_id` and normalized
  semantic result.
- `timestamp_ms` is session-relative elapsed time.
- `capture_started_at` and `capture_ended_at` are optional wall-clock metadata.
- `server_received_at` is Cloud ingest time only.
- ESP firmware contract remains unchanged; `honda_keihin_legacy_29` is transport
  compatibility metadata, not Cloud semantic decoder provenance.

## Runtime Diagram

```text
ESP
 │ RAW29
 ▼
Cloud
 ├─ store RAW
 └─ Analyzer
      ├─ normalize native24
      ├─ decode V2
      └─ analyze
             ▼
           Cloud

Pi
 │ RAW24 + V2 + analysis
 ▼
Cloud
 └─ store only
```
