# Analysis Persistence

## What Cloud Stores

Cloud stores one row per Analyzer summary in `AnalysisResult`.

Fields:

- `ride_session_id`
- `session_id`
- `analysis_run_id`
- `model_version`
- `feature_schema_version`
- `overall_status`
- `health_score`
- `anomaly_ratio`
- `anomaly_window_count`
- `result_summary`
- `analyzed_at`
- `created_at`

`RideSession` also stores:

- `analysis_status`
- latest `analysis_run_id`

Cloud does not persist Analyzer windows, features, per-window scores, model
artifact metadata beyond summary fields, or Analyzer file-backed artifacts.

## Multiple Runs

Multiple `AnalysisResult` rows can belong to one `RideSession`. The only unique
constraint is `analysis_run_id`. Manual rerun can create another row, and
`RideSession.analysis_run_id` is updated to the newest successful run.

## When Analyzer Runs

Cloud runs Analyzer:

- automatically after final upload if `ML_API_BASE_URL` is configured;
- manually through `/api/sessions/<id>/analysis/re-run`.

Cloud does not rerun Analyzer merely because a user opens a page.

## How Analysis Reaches Frontend

Frontend pages call `services.analysis.analysis_snapshot`.

That snapshot combines:

- local per-sample statistical screening from stored `TelemetryRecord` rows;
- optional embedded sample score metadata read from `raw_frame` object keys;
- latest `AnalysisResult` for external summary fields.

The UI reads:

- `/api/sessions/<id>/samples` for chart samples/events;
- `/api/sessions/<id>/analysis` for analysis snapshot plus ML service status;
- `/analysis` for the analysis overview;
- `/api/analysis/status` for Analyzer health.

## Can Existing UI Display A Session If Cloud Did Not Run Analyzer?

Yes, if the session has `RideSession` and `TelemetryRecord` rows. The UI will
fall back to local statistical screening and can still show charts, events, CSV,
JSON, and session pages.

Can it display a Pi-generated external analysis without Cloud rerunning
Analyzer? Partially, but there is no current device API to create
`AnalysisResult` from a trusted uploaded summary. If an `AnalysisResult` row
already exists, the dashboard can surface its model/version/status/health fields.
However charts/events still depend on stored telemetry samples, and the current
analysis snapshot computes many values from `TelemetryRecord`.

## Pi Implication

Pi MVP should sync enough decoded telemetry for the existing UI to build charts
and summaries, plus a trusted imported `AnalysisResult` summary with decoder and
model provenance. Syncing only an analysis summary is not enough for the current
frontend experience.

