# Pi5 Reuse Guide

This guide identifies what a future Raspberry Pi 5 component should reuse from
this repository. It does not define UART, K-Line, GPIO, serial scheduling, or
hardware capture behavior.

## Preferred Boundary

The preferred boundary is canonical telemetry:

```python
from app.domain.telemetry import CanonicalTelemetrySession
from app.services.analysis_service import AnalysisService

session = CanonicalTelemetrySession(...)
output = AnalysisService(settings).analyze(session, persist=False)
```

Use this path if Pi5 code already has decoded ECU signals.

## RAW Compatibility Reuse

If Pi5 has a saved legacy text or ESP JSONL raw file supported by this repo:

```python
from app.config import Settings
from app.ingestion.raw_ecu.adapter import import_raw_file
from app.services.analysis_service import AnalysisService

settings = Settings(
    data_dir=...,
    model_dir=...,
)

raw_import = import_raw_file(
    path,
    settings,
    session_id="...",
    device_id="...",
    vehicle_id="...",
    sample_interval_ms=100,
)

output = AnalysisService(settings).analyze(
    raw_import.canonical_session,
    process_with_model=True,
    persist=False,
)
```

For in-memory legacy text:

```python
from app.ingestion.raw_ecu.adapter import import_raw_text

raw_import = import_raw_text(text, settings, sample_interval_ms=100)
```

## Minimal Modules to Reuse

| Need | Module |
| --- | --- |
| Canonical schema | `app.domain.telemetry` |
| Raw text/JSONL import to canonical | `app.ingestion.raw_ecu.adapter` |
| Current runtime legacy 29-byte parser/checksum/decoder | `app.ingestion.raw_ecu.*` |
| Canonical analysis | `app.services.analysis_service.AnalysisService` |
| Windowing/features/inference internals | `app.ml.validation`, `app.ml.windowing`, `app.ml.features`, `app.ml.inference` |
| Settings | `app.config.Settings` |
| Optional persistence | `app.repositories.file_repository.FileBackedRepository` |

## Lifecycle

1. Create a `Settings` object.
2. Ensure model artifacts exist in `settings.model_dir` if scoring is required.
3. Build or import a `CanonicalTelemetrySession`.
4. Call `AnalysisService.analyze`.
5. Use `output.summary` for high-level status and `output.windows` for window-level details.

When `persist=False`, analysis does not write repository entities. When
`persist=True` and a repository is supplied, it writes local file-backed
entities/telemetry/analysis artifacts.

## Model Artifact Requirement

For inference, Pi5 must have all of:

```text
isolation_forest.joblib
robust_scaler.joblib
model_metadata.json
```

If artifacts are absent, analysis still validates/windows/features the session
but returns:

```text
model_loaded = false
overall_status = model_unavailable
health_score = null
```

## Decoder Provenance Requirement

The analyzer checks model metadata `training_decoder_versions` against:

```text
{session.decoder_id}:{session.decoder_version}
```

If they do not match, analysis succeeds but returns a warning. Pi5 should
preserve decoder identity/version accurately in canonical telemetry.

Current runtime RAW adapter emits:

```text
honda_keihin_legacy_29:0.1.0
```

Native Pi V2 training emits:

```text
honda_keihin_71_17:1.0.0
```

Do not mix model artifacts and canonical sessions across decoder versions
without expecting compatibility warnings or feature/schema failures.

## Native V2 Contract

Pi native code should target:

```text
telemetry_schema_version = canonical-telemetry-v2
model_dir = data/models/honda_keihin_71_17_v2
```

Required V2 signals:

```text
rpm
tps_voltage
tps_raw
battery_voltage
iat_c
ect_c
```

Use the native adapter for offline replay/tests:

```python
from app.ingestion.honda_0x17.adapter import import_native_jsonl_file
```

Do not add five leading `FF` bytes for Pi native frames.

## Recommended Pi5 Integration Choices

### If Pi5 Captures Legacy-Compatible RAW Files

Use:

```text
import_raw_file/import_raw_text -> AnalysisService.analyze
```

This avoids duplicating the current runtime parser, decoder, canonical adapter,
validation, windowing, and feature extraction.

### If Pi5 Owns Decoding

Build `CanonicalTelemetrySession` directly and call:

```text
AnalysisService.analyze
```

Pi5 must preserve:

- `decoder_id`
- `decoder_version`
- `ecu_profile_id`
- `telemetry_schema_version`
- per-sample `sequence`
- per-sample `frame_valid`
- per-sample `checksum_valid`
- required core signals

### If Pi5 Captures Native 24-Byte Honda Frames

Build `canonical-telemetry-v2` directly or use `import_native_jsonl_file` for
offline replay. Analyze with `AnalysisService(..., model_dir="data/models/honda_keihin_71_17_v2")`.

## Non-Goals for Pi5 from This Repository

This repository does not specify:

- serial port names,
- GPIO control,
- K-Line wakeup timing,
- ESP firmware protocol,
- Pi service manager,
- offline sync,
- Cloud authentication,
- Cloud upload envelopes,
- OTA/update behavior.
