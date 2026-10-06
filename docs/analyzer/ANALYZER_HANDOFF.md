# Analyzer Handoff

Use this file as the short handoff for future Cloud and Pi tasks.

## LEGACY / ESP COMPATIBILITY CONTRACT

Purpose:

```text
ESP / legacy Cloud uploads -> canonical-telemetry-v1 -> legacy model/runtime
```

Input:

```text
legacy 29-byte compatibility frame
FF FF FF FF FF 02 18 71 17 ... checksum
```

Checksum:

```text
sum(frame) % 256 == 251
```

Canonical schema:

```text
canonical-telemetry-v1
```

Decoder provenance:

```text
ecu_profile_id = honda_keihin_legacy_29
decoder_id = honda_keihin_legacy_29
decoder_version = 0.1.0
```

Required V1 ML signals:

```text
rpm
tps_voltage
tps_raw_candidate
battery_voltage
iat_c
ect_c_candidate
map_raw
```

V1 remains compatibility-only. Do not reinterpret `ect_c_candidate` or
`map_raw` under existing consumers.

## PI5 / NATIVE HONDA CONTRACT

Purpose:

```text
Pi5 native Honda Table 0x17 -> canonical-telemetry-v2 -> Pi V2 model/runtime
```

Input:

```text
24-byte native frame
02 18 71 17 ... checksum
```

Checksum:

```text
sum(frame) % 256 == 0
expected_checksum = (-sum(frame[:23])) & 0xFF
```

Pi must not add five leading `FF` bytes.

Canonical schema:

```text
canonical-telemetry-v2
```

Decoder provenance:

```text
ecu_profile_id = honda_keihin_71_17
decoder_id = honda_keihin_71_17
decoder_version = 1.0.0
```

Required V2 ML signals:

```text
rpm
tps_voltage
tps_raw
battery_voltage
iat_c
ect_c
```

Optional candidate metadata:

```text
tps_percent_calibrated
iat_voltage_candidate
ect_voltage_candidate
injector_raw
injector_ms_candidates
byte17
byte18
unknown_reserved_raw
checksum_byte
```

## Analysis Entry Point

HTTP:

```text
POST /api/v1/analysis
```

Python:

```python
AnalysisService(settings, model_dir=...).analyze(canonical_session)
```

V1 and V2 use the same `AnalysisService`; telemetry schema selects required
signals and feature columns.

Native import helper:

```python
from app.ingestion.honda_0x17.adapter import import_native_jsonl_file
```

## Analysis Output Contract

Summary keys:

```text
analysis_run_id
session_id
model_version
telemetry_schema_version
feature_schema_version
signal_columns
window_count
anomaly_window_count
anomaly_ratio
health_score
overall_status
most_unusual_features
model_loaded
warnings
note
```

## Model Provenance

V1 default artifacts:

```text
data/models/
```

V2 Pi-compatible artifacts:

```text
data/models/honda_keihin_71_17_v2/
```

V2 model:

```text
model_version_id = iforest-baseline-20260920T091709Z
telemetry_schema_version = canonical-telemetry-v2
feature_schema_version = ecu-window-features-v1
training_decoder_versions = ["honda_keihin_71_17:1.0.0"]
training_window_count = 3874
feature_count = 81
```

The model is an unsupervised anomaly baseline, not a mechanical fault
diagnosis model.

## Compatibility Behavior

- V1 + V1-compatible model works as before.
- V2 + V2-compatible model works with explicit `model_dir`.
- V2 against a V1 model fails clearly via telemetry schema, signal column, or
  feature-name mismatch.
- The API endpoint remains `/api/v1/analysis`; telemetry schema version is
  independent of API version.

## Open Questions Outside This Repo

- Pi UART/K-Line/GPIO capture implementation.
- Cloud sync envelope and auth.
- Wall-clock capture metadata.
- Future MAP identity/scale.
- Whether additional ECU commands should be decoded later.
