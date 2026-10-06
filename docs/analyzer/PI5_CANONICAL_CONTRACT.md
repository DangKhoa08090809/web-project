# Pi5 Canonical Contract

This is the primary Analyzer-side contract for future Raspberry Pi 5 work.
It defines the native Honda Table `0x17` telemetry and analysis boundary.

## Native Frame Format

Pi5 must use native ECU frames:

```text
24 bytes
02 18 71 17 ... checksum
```

Validation:

```text
length == 24
header == 02 18 71 17
sum(frame) % 256 == 0
expected_checksum = (-sum(frame[:23])) & 0xFF
```

Pi5 must not generate or require the five leading `FF` bytes. That prefix is
legacy Cloud/ESP compatibility only.

## Decoder Identity

Production native decoder provenance:

```text
ecu_profile_id = honda_keihin_71_17
decoder_id = honda_keihin_71_17
decoder_version = 1.0.0
evidence_profile = honda_keihin_71_17_v0.2
```

Implementation source:

```text
app.ecu.profiles.honda_keihin_71_17.decode_production_values
app.ingestion.honda_0x17.adapter
```

## Canonical Schema

```text
telemetry_schema_version = canonical-telemetry-v2
```

Required V2 sample signals:

```text
rpm
tps_voltage
tps_raw
battery_voltage
iat_c
ect_c
```

Required metadata:

```text
session_id
ecu_profile_id
decoder_id
decoder_version
telemetry_schema_version
sampling
samples
signal_definitions
source_type
```

Recommended `source_type`:

```text
native_honda_0x17
```

## Optional Candidate Signals

Uncertain or vehicle-specific values must stay in `candidate_signals`, not in
the required ML core:

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

Deliberately excluded from V2 required core:

```text
map_raw
tps_percent_calibrated
injector_ms
byte17
byte18_speed
```

## Signal Confidence

| Signal | Native source | Formula | Confidence |
| --- | --- | --- | --- |
| `rpm` | bytes 4-5 | big-endian u16 | VERIFIED |
| `tps_voltage` | byte 6 | `raw * 5 / 256` | VERIFIED |
| `tps_raw` | byte 7 | raw | VERIFIED |
| `battery_voltage` | byte 14 | `raw / 10` | VERIFIED |
| `iat_c` | byte 11, with byte 10 voltage candidate | `byte11 - 40` | PROVISIONAL / high-confidence |
| `ect_c` | byte 13, with byte 12 voltage candidate | `byte13 - 40` | PROVISIONAL / high-confidence |

## Timestamp Semantics

`timestamp_ms` is session-local/source-relative elapsed milliseconds. It is not
a wall-clock timestamp.

The native JSONL helper accepts:

```text
elapsed_ms -> timestamp_ms -> device_time_ms -> host_monotonic_ms
```

Pi/Cloud sync must handle wall-clock capture metadata separately.

## Analysis Entry Point

Python:

```python
from app.ingestion.honda_0x17.adapter import import_native_jsonl_file
from app.services.analysis_service import AnalysisService

native = import_native_jsonl_file(path, session_id="...", sample_interval_ms=250)
result = AnalysisService(settings, model_dir="data/models/honda_keihin_71_17_v2").analyze(
    native.canonical_session,
    process_with_model=True,
    persist=False,
)
```

Direct canonical:

```python
session = CanonicalTelemetrySession(telemetry_schema_version="canonical-telemetry-v2", ...)
result = AnalysisService(settings, model_dir="data/models/honda_keihin_71_17_v2").analyze(session)
```

HTTP:

```text
POST /api/v1/analysis
```

The API version remains `/api/v1`; telemetry schema version is independent.

## Feature Schema

```text
feature_schema_version = ecu-window-features-v1
```

For V2 selected core:

```text
6 signals * 13 statistical features + 3 relation features = 81 features
```

V2 relation features:

```text
corr_rpm_tps_voltage
corr_rpm_tps_raw
rpm_per_tps_voltage
```

No MAP relation features are generated for V2.

## Model Artifact

Pi-compatible baseline artifacts:

```text
data/models/honda_keihin_71_17_v2/isolation_forest.joblib
data/models/honda_keihin_71_17_v2/robust_scaler.joblib
data/models/honda_keihin_71_17_v2/model_metadata.json
```

Model version:

```text
iforest-baseline-20260920T091709Z
```

Training provenance:

```text
telemetry_schema_version = canonical-telemetry-v2
decoder_id = honda_keihin_71_17
decoder_version = 1.0.0
training_decoder_versions = ["honda_keihin_71_17:1.0.0"]
training_sessions = 9 real_data/real_run JSONL files
training_window_count = 3874
feature_count = 81
```

This is an unsupervised anomaly baseline, not a mechanical fault classifier.

## Result Schema

`AnalysisService.analyze(...).summary` includes:

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

`health_score` is an internal normalized score, not a fault probability.

## Compatibility Rule

V2 telemetry should be scored with the V2 model directory explicitly. The
runtime checks model metadata and feature names. A V1 model used with V2
telemetry fails with a clear schema/signal/feature mismatch.
