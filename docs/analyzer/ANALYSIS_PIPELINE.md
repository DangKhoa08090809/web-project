# Analysis Pipeline Contract

Source of truth:

- `app.services.analysis_service`
- `app.ml.validation`
- `app.ml.windowing`
- `app.ml.features`
- `app.ml.inference`
- `app.ml.training`
- `app.ml.scoring`

## Primary Entry Point

```python
AnalysisService(settings, repository=None, model_dir=None).analyze(
    canonical_session,
    process_with_model=True,
    persist=True,
)
```

Input:

- `canonical_session`: `CanonicalTelemetrySession`
- `process_with_model`: when false, validation/windows/features still run, but
  inference is skipped and status is `not_scored`
- `persist`: when true and a repository exists, writes session, telemetry,
  decoder/model versions, analysis run, and analysis windows

Output:

```python
AnalysisServiceOutput(
    analysis_run_id: str,
    session_id: str,
    frame_data: pandas.DataFrame,
    feature_frame: pandas.DataFrame,
    windows: pandas.DataFrame,
    summary: dict,
    warnings: list[str],
)
```

HTTP wrapper:

```text
POST /api/v1/analysis
```

returns `output.summary`.

## Validation

`validate_canonical_session_for_ml` enforces:

- `feature_schema_version == "ecu-window-features-v1"`
- `samples` is not empty
- `len(samples) >= settings.window_size_samples`
- `sequence` values are unique and monotonic increasing
- non-null `timestamp_ms` values are monotonic increasing
- eligible samples are samples with `frame_valid and checksum_valid`
- eligible sample count is at least `settings.window_size_samples`
- every core signal is present and finite for every eligible sample

Current required core signals:

```text
rpm
tps_voltage
tps_raw_candidate
battery_voltage
iat_c
ect_c_candidate
map_raw
```

Validation raises `MLInputValidationError`, exposed as HTTP 422 by
`POST /api/v1/analysis`.

## Frame Data

`canonical_session_to_frame_data` creates one row per sample:

```text
frame_index
sequence
relative_time_ms
checksum_valid
is_valid
ml_eligible
rpm
tps_voltage
tps_raw_candidate
battery_voltage
iat_c
ect_c_candidate
map_raw
```

`relative_time_ms` is copied from sample `timestamp_ms`.

## Windowing

Function: `create_windows`

Default runtime settings:

| Setting | Default |
| --- | --- |
| `WINDOW_SIZE_SAMPLES` | `50` |
| `WINDOW_STEP_SAMPLES` | `10` |
| `MAX_WINDOW_CHECKSUM_ERROR_RATIO` | `0.0` |
| `MAX_WINDOW_INVALID_DECODED_RATIO` | `0.0` |

Rules:

- Windowing is sample-count based, not seconds-based.
- `window_size_samples` and `step_size_samples` must be positive.
- Empty data or fewer rows than one window returns no windows.
- Each window records row range, frame index range, sample count, checksum
  failure ratio, invalid decoded ratio, and optional `duration_ms`.
- A window is skipped when checksum failure ratio or invalid decoded ratio
  exceeds the configured max.

`duration_ms` is present only when every row in the window has
`relative_time_ms`.

## Feature Extraction

Function: `extract_window_features`

Per-signal statistical features:

```text
mean
std
min
max
range
median
first
last
delta
slope
mean_absolute_diff
max_absolute_diff
constant_signal
```

Relation features added when required columns exist:

```text
corr_rpm_tps_voltage
corr_rpm_tps_raw
corr_rpm_map
corr_tps_map
rpm_per_tps_voltage
```

For the current 7 core signals, the default feature schema has:

```text
7 signals * 13 statistical features + 5 relation features = 96 model features
```

Feature metadata columns:

```text
window_index
start_frame_index
end_frame_index
sample_count
checksum_failure_ratio
invalid_decoded_ratio
duration_ms
```

Feature cleaning behavior:

- Values are coerced to numeric.
- All-missing series become zeros.
- Partial missing series are forward-filled, back-filled, then filled with zero.
- Infinite and NaN feature values are replaced with zero.
- Correlations return zero for constant/too-short/non-finite series.

## Model Artifacts

Artifact directory defaults to `settings.model_dir`:

```text
isolation_forest.joblib
robust_scaler.joblib
model_metadata.json
```

All three files must exist for `model_artifacts_exist` to be true.

Training uses:

- `sklearn.preprocessing.RobustScaler`
- `sklearn.ensemble.IsolationForest`
- default `n_estimators=200`
- default `contamination=0.03`
- default `random_state=42`

Model metadata includes:

- `model_name = ecu-anomaly-baseline`
- `model_family = IsolationForest`
- `model_type = IsolationForest`
- `model_version_id`
- `feature_names`
- `signal_columns`
- `feature_schema_version`
- `training_sessions`
- `training_decoder_versions`
- `window_size_samples`
- `window_step_samples`
- hyperparameters
- training score percentiles
- feature medians and IQRs

## Inference

Function: `run_inference`

Behavior:

| Condition | Result |
| --- | --- |
| No feature rows | `overall_status="no_windows"`, no health score. |
| Missing model artifacts | `overall_status="model_unavailable"`, no health score. |
| Model feature schema mismatch | Raises `ValueError`. |
| Runtime feature frame missing model features | Raises `ValueError`. |
| Model available | Scales features, runs Isolation Forest, scores windows. |

Per-window output columns added when a model is loaded:

```text
score_sample
decision_score
prediction
is_anomaly
window_health_score
most_unusual_features
```

`prediction == -1` means Isolation Forest classified that window as anomalous.

## Health Score and Status

`window_health_score` maps Isolation Forest `score_samples` to an internal
0..100 scale using training score percentiles. It is not a failure probability.

Session health combines:

- median window health: 65%
- lower-decile window health: 20%
- anomaly-ratio penalty component: 15%

Final status:

| Condition | Status |
| --- | --- |
| no health score | `no_windows` |
| `anomaly_ratio >= 0.15` or `health < 50` | `attention` |
| `anomaly_ratio >= 0.02` or `health < 80` | `monitor` |
| otherwise | `ok` |

When model artifacts are unavailable, status is `model_unavailable`.

## Analysis Summary Contract

`AnalysisService.analyze(...).summary` contains:

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

Warnings include decoder/model provenance issues, for example:

- model metadata lacks `training_decoder_versions`
- telemetry decoder was not present in model training provenance

## RAW Session Result Contract

RAW upload/session processing returns:

```text
session_id
analysis_run_id
created_at
status
input
statistics
result
artifacts
processing_duration_seconds
```

`input` includes:

```text
filename
device_id
vehicle_id
sample_interval_ms
sampling_rate_hz
session_note
firmware_version
client_session_id
upload_sha256
source_format
time_basis
```

`statistics` includes parser statistics plus:

```text
decoded_valid_frames
decoded_invalid_frames
windows_generated
anomaly_windows
```

`result` includes:

```text
overall_status
health_score
anomaly_ratio
most_unusual_features
model_loaded
model_version
feature_schema_version
warnings
note
```

## Pi Native V2 Training Path

`app.ml.profile_v02_training` now trains the Pi/native V2 baseline from native
Honda Table `0x17` JSONL. It uses these selected production core signal columns:

```text
rpm
tps_voltage
tps_raw
battery_voltage
iat_c
ect_c
```

For those 6 columns, relation features exclude MAP relations and produce:

```text
6 signals * 13 statistical features + 3 relation features = 81 model features
```

This path writes Pi V2 reports and trains a model under:

```text
data/models/honda_keihin_71_17_v2
```

The model metadata records `telemetry_schema_version=canonical-telemetry-v2`,
`decoder_id=honda_keihin_71_17`, and `decoder_version=1.0.0`.
