# Analyzer Overview

This document audits only the `ecuread-analyzer` repository. It does not assume
access to Cloud Server/Web, ESP32 node, or future Raspberry Pi 5 source code.

## Role

`ecuread-analyzer` is a FastAPI and Python-library component that:

1. Accepts canonical telemetry directly, or accepts RAW ECU logs through a
   compatibility adapter.
2. Converts supported RAW inputs into `CanonicalTelemetrySession`.
3. Validates canonical telemetry for ML.
4. Builds sample-count windows and statistical features.
5. Runs an experimental Isolation Forest baseline when model artifacts exist.
6. Persists local file-backed entities, telemetry, analysis runs, and CSV/debug
   artifacts.

The analyzer is not a hardware communication implementation and is not a
validated mechanical diagnostic system.

## Important Repository Structure

| Area | Responsibility |
| --- | --- |
| `app/main.py` | FastAPI application factory and router wiring. |
| `app/api/` | HTTP endpoints for health, raw uploads, canonical analysis, sessions, and model info/training. |
| `app/domain/telemetry.py` | Canonical telemetry schema and signal definitions. |
| `app/domain/model.py` | Feature/model registry schema constants. |
| `app/ingestion/raw_ecu/` | Current runtime RAW parser, checksum, legacy 29-byte decoder, validation, and canonical adapter. |
| `app/ingestion/honda_0x17/` | Native 24-byte Honda Table `0x17` adapter that emits `canonical-telemetry-v2`. |
| `app/ecu/` | Compatibility shims plus the audited Honda/Keihin `0x71/0x17` profile module. |
| `app/ml/` | Schema-aware canonical validation, windowing, feature extraction, Isolation Forest training/inference/scoring, and Pi V2 model training path. |
| `app/services/` | Orchestration for analysis, raw session processing, storage, and training. |
| `app/repositories/` | File-backed logical entities for sessions, raw artifacts, decoder versions, model versions, telemetry, and analyses. |
| `tests/` | Contract/regression tests for parser, checksum, decoder, canonical API, persistence, ML pipeline, profile v0.2, and audit tools. |
| `data/decoder-audit/` | Decoder audit reports, profile JSON, plots, and profile v0.2 ML training outputs. |
| `real_data/` | Real-run JSONL and calibration datasets used by audit/training tests and reports. |

## Runtime Entry Points

### HTTP

| Endpoint | Contract |
| --- | --- |
| `GET /health` | Service status plus model-artifact availability. |
| `POST /api/v1/analysis` | Preferred analysis path. Input is `CanonicalTelemetrySession`. |
| `POST /api/v1/sessions` | RAW compatibility upload. Multipart file plus metadata. |
| `POST /api/v1/import/raw-session` | Same RAW compatibility processing path, explicitly named for raw imports. |
| `GET /api/v1/sessions...` | Session, telemetry, frames, windows, and session analysis lookup. |
| `GET /api/v1/analyses...` | Analysis run and analysis window lookup. |
| `GET /api/v1/model` | Model artifact metadata. |
| `POST /api/v1/model/train` | Disabled by default unless `ALLOW_MODEL_TRAINING_API=true`. |

### Python

The smallest current library entry points are:

```python
from app.ingestion.raw_ecu.adapter import import_raw_file, import_raw_text
from app.services.analysis_service import AnalysisService

raw_import = import_raw_file(path, settings, sample_interval_ms=100)
output = AnalysisService(settings).analyze(raw_import.canonical_session, persist=False)
```

For already-decoded data:

```python
from app.domain.telemetry import CanonicalTelemetrySession
from app.services.analysis_service import AnalysisService

session = CanonicalTelemetrySession(...)
output = AnalysisService(settings).analyze(session, persist=False)
```

## Pipeline Map

```text
RAW ECU data
  -> app.ingestion.raw_ecu.parser
  -> app.ingestion.raw_ecu.checksum
  -> app.ingestion.raw_ecu.decoder
  -> app.ingestion.raw_ecu.validation
  -> app.ingestion.raw_ecu.adapter
  -> app.domain.telemetry.CanonicalTelemetrySession
  -> app.ml.validation
  -> app.ml.windowing
  -> app.ml.features
  -> app.ml.inference
  -> app.domain.analysis.AnalysisServiceOutput / persisted analysis run
```

Pi native path:

```text
24-byte Honda Table 0x17 frame
  -> app.ecu.profiles.honda_keihin_71_17
  -> app.ingestion.honda_0x17.adapter
  -> canonical-telemetry-v2
  -> app.services.analysis_service.AnalysisService
  -> validation -> windows -> features -> inference
```

Canonical-only path:

```text
CanonicalTelemetrySession
  -> app.services.analysis_service.AnalysisService.analyze
  -> validation -> windows -> features -> inference -> AnalysisResult summary
```

## Boundary Finding

The intended boundary is `CanonicalTelemetrySession`.

Verified:

- `app.services.analysis_service` consumes canonical telemetry and does not
  import the RAW parser or legacy decoder.
- `app.ml.validation`, `app.ml.windowing`, `app.ml.features`, `app.ml.inference`,
  and `app.ml.training` operate on canonical/dataframe features, not RAW bytes.
- RAW compatibility is converted to canonical telemetry before analysis.

Exceptions/compatibility paths:

- `app.services.training_service.train_from_raw_files` imports the RAW adapter
  to convert raw files before training.
- `app.ml.profile_v02_training` imports `app.ecu.profiles.honda_keihin_71_17`
  directly to train the Pi/native V2 model from real-run JSONL files.
- `app.ecu.decoder`, `app.ecu.parser`, `app.ecu.checksum`, and
  `app.ecu.validation` are compatibility shims around `app.ingestion.raw_ecu`
  modules.

## Persistence

When persistence is enabled, analysis writes:

- `data/entities/sessions/{session_id}.json`
- `data/entities/decoder_versions/{decoder_id}_{version}.json`
- `data/entities/model_versions/{model_version_id}.json` when a model is loaded
- `data/telemetry/{session_id}/metadata.json`
- `data/telemetry/{session_id}/samples.jsonl`
- `data/analyses/{analysis_run_id}/analysis_run.json`
- `data/analyses/{analysis_run_id}/analysis_windows.jsonl`
- `data/analyses/{analysis_run_id}/window_features.jsonl`

RAW upload processing also writes:

- `data/raw/{session_id}/original.txt`
- `data/raw/{session_id}/upload_metadata.json`
- `data/processed/{session_id}/frames.csv`
- `data/processed/{session_id}/windows.csv`
- `data/results/{session_id}/result.json`
- `data/entities/raw_artifacts/{artifact_id}.json`

## Verified Limitations

- The repository does not contain Cloud or ESP32 implementation contracts.
- The repository does not implement UART, K-Line wakeup, live serial capture, or
  Pi5 hardware communication.
- Legacy RAW compatibility remains `honda_keihin_legacy_29:0.1.0`.
- Native Pi/Honda Table `0x17` runtime is additive:
  `honda_keihin_71_17:1.0.0` -> `canonical-telemetry-v2`.
- Model outputs are experimental anomaly scores and internal health scores, not
  mechanical fault probabilities.
