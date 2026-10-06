# H9B Diagnostic Evidence Consumer

## Analyzer Ingress Map

Analyzer output enters DriveSafe through two durable paths:

- ESP RAW sessions are submitted from `services.telemetry.store_upload()` after final upload, then reprocessed in `services.analysis_submission.submit_session_to_analyzer()`.
- Pi-native sessions import their local analyzer result through `services.pi_sync.validate_pi_sync()` and `services.pi_sync.store_pi_sync()`.

Both paths persist analyzer results in `AnalysisResult`. Production-facing fields remain the existing columns:

- `overall_status`
- `health_score`
- `anomaly_ratio`
- `anomaly_window_count`
- `model_version`
- schema and decoder provenance columns

The full analyzer summary is also stored in `AnalysisResult.result_summary`. H9B uses that existing JSON persistence for additive `diagnostic_evidence` instead of adding relational detector/RCA tables.

## Serialization And UI

Session detail APIs are built by `services.analysis.analysis_snapshot()`, which now exposes:

- `diagnostic_evidence`: the raw analyzer-provided evidence object
- `diagnostic_evidence_view`: a display-only view model for the session-detail template

The session-detail page renders a collapsed "Additional diagnostic evidence" section only when valid evidence is present. The section keeps analyzer-provided evidence state, observations, symptoms, recommended-check priority, detector status, versions, and provenance separate from the primary production analysis.

## Integrity Rules

DriveSafe does not recompute detector evidence, RCA v2, recommended-check priority, or historical evidence. It preserves analyzer-provided provenance and cutoff metadata in `result_summary`.

Malformed optional `diagnostic_evidence` is ignored with an analysis warning where possible, while the production analysis result remains valid.

Re-analysis creates a separate `AnalysisResult` when the analyzer emits a new `analysis_run_id`; old stored `result_summary` JSON is not recomputed on page load.
