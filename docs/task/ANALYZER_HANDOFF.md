# Analyzer Handoff

Format: concise handoff for future Cloud/Pi5 tasks.

## Analyzer Responsibilities

- Accept canonical telemetry through `POST /api/v1/analysis` or Python
  `AnalysisService.analyze`.
- Accept supported RAW files through `/api/v1/sessions`,
  `/api/v1/import/raw-session`, `import_raw_file`, or `import_raw_text`.
- Convert supported RAW into `CanonicalTelemetrySession`.
- Validate canonical telemetry for ML.
- Build sample-count windows and statistical features.
- Run Isolation Forest inference when complete model artifacts exist.
- Persist local file-backed entities and artifacts when requested.

## Raw Input Contract

Supported RAW formats:

- Legacy text lines with `RAW:` and 29 semicolon-separated 1-2 digit hex bytes.
- ESP JSONL, one JSON object per line, with `raw_hex`.

JSONL `raw_hex`:

- accepts compact hex or separated hex tokens,
- accepts 24-byte normalized frames,
- accepts 29-byte frames only with five leading `FF` bytes,
- optional `raw_length` must match parsed length.

JSONL timestamps:

```text
elapsed_ms -> timestamp_ms -> device_time_ms
```

Runtime legacy checksum:

```text
29 bytes, sum(frame) % 256 == 251
```

Audited 24-byte profile checksum:

```text
24 bytes, header 02 18 71 17, sum(frame) % 256 == 0
```

Malformed JSONL upload is rejected before raw/session commit. Malformed legacy
text frames are preserved, but analysis still needs enough eligible samples.

## Canonical Telemetry Contract

Schema version:

```text
canonical-telemetry-v1
```

Session type:

```text
app.domain.telemetry.CanonicalTelemetrySession
```

Required core ML signals in eligible samples:

```text
rpm
tps_voltage
tps_raw_candidate
battery_voltage
iat_c
ect_c_candidate
map_raw
```

Eligible sample:

```text
frame_valid == true and checksum_valid == true
```

Decoder provenance fields:

```text
ecu_profile_id
decoder_id
decoder_version
decoder_version_key = "{decoder_id}:{decoder_version}"
```

## Analysis Entry Point

Python:

```python
AnalysisService(settings, repository=None, model_dir=None).analyze(
    canonical_session,
    process_with_model=True,
    persist=True,
)
```

HTTP:

```text
POST /api/v1/analysis
```

RAW compatibility:

```python
raw_import = import_raw_file(path, settings, ...)
output = AnalysisService(settings).analyze(raw_import.canonical_session)
```

## Analysis Output Contract

Summary keys:

```text
analysis_run_id
session_id
model_version
feature_schema_version
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

Feature schema:

```text
ecu-window-features-v1
```

Possible no-model status:

```text
overall_status = model_unavailable
model_loaded = false
health_score = null
```

Model-loaded statuses:

```text
ok
monitor
attention
no_windows
```

Health score is internal 0..100, not a fault probability.

## Decoder Provenance

Current runtime RAW adapter:

```text
ecu_profile_id = honda_keihin_legacy_29
decoder_id = honda_keihin_legacy_29
decoder_version = 0.1.0
```

Audited profile/training path:

```text
profile_id = honda_keihin_71_17_v0.2
decoder_id = honda_keihin_71_17
decoder_version = 0.2
```

Verified runtime-compatible mappings:

- `rpm`
- `tps_voltage`
- `tps_raw_candidate`
- `battery_voltage`

Provisional/unknown runtime mappings:

- `iat_c`: provisional/high-confidence.
- `ect_c_candidate`: unknown as current V1 field; profile v0.2 points engine
  temperature to normalized byte 13 instead.
- `map_raw`: unknown as MAP; profile v0.2 rejects old MAP hypothesis.
- `signal_b19`, `signal_word_20_21`, `signal_b22`, `signal_b23`,
  `signal_b24`: unknown.

## Model Provenance

Default artifacts:

```text
data/models/isolation_forest.joblib
data/models/robust_scaler.joblib
data/models/model_metadata.json
```

Model metadata records:

- `feature_schema_version`
- `feature_names`
- `training_sessions`
- `training_decoder_versions`
- window settings
- Isolation Forest hyperparameters
- training score percentiles
- feature medians/IQRs

Profile v0.2 artifact path exists:

```text
data/models/honda_keihin_71_17_v0.2_real_run
```

That model uses a different signal list and must not be assumed compatible with
canonical V1 without schema/model alignment.

## Known Limitations

- No Cloud source contract in this repository.
- No ESP source contract in this repository.
- No Pi5 hardware/serial implementation.
- Current generic runtime canonical schema is V1 legacy-compatible.
- Profile v0.2 evidence is present but not fully integrated into generic
  `AnalysisService` canonical input.
- RAW runtime legacy decoder does not enforce header/profile identity.
- Model is an experimental unsupervised baseline.

## Open Questions

- Final Cloud API envelope, auth, idempotency, and storage semantics.
- ESP/Pi raw capture transport and clock semantics.
- Whether canonical V1 should be migrated to profile v0.2 fields.
- How to version multiple ECU profiles in production.
- Which model artifact should ship to Pi5 for offline scoring.
- Whether runtime should enforce `0x71/0x17` header/profile validation before
  canonical conversion.
