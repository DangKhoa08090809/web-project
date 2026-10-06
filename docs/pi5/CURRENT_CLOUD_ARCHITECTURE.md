# Current Cloud Architecture

This audit covers the Cloud Server/Web repository only. The attached ESP and
Analyzer documents are external contracts, not instructions to change this
codebase.

## Runtime Shape

- Backend entry point: `app.py:create_app()`. It creates the Flask app,
  loads `Config`, initializes SQLAlchemy, Flask-Migrate, Flask-Login, and CSRF,
  registers blueprints, and exposes `/health`.
- Production entry point: `Dockerfile` runs `flask --app app db upgrade` and
  Gunicorn against `app:create_app()`.
- Frontend entry point: server-rendered Jinja templates under `templates/`
  with static scripts under `static/js/`. There is no package-managed frontend
  build in this repo.
- Database: SQLAlchemy models in `models.py`, migrations in
  `migrations/versions/`.
- Background workers/jobs: no queue worker is implemented. Analyzer submission
  is synchronous after final upload when `ML_API_BASE_URL` is configured.
  MQTT is only a placeholder module. Live Preview uses Redis or in-memory
  pub/sub, not a persistent background job.

## Blueprints And Routes

| Area | Code | Main routes |
| --- | --- | --- |
| Auth | `routes/auth.py` | `/auth/login`, `/auth/logout`, `/auth/me`, `/auth/csrf` |
| Dashboard/Web | `routes/dashboard.py` | `/`, `/devices`, `/sessions`, `/sessions/<id>`, `/analysis`, `/compare`, `/tool`, `/settings` |
| Browser JSON | `routes/dashboard.py` | `/api/sessions`, `/api/sessions/<id>/samples`, `/api/sessions/<id>/analysis`, `/api/analysis/status`, `/api/compare`, `/api/devices` |
| Device ingest | `routes/ingest.py` | `/api/device/pair`, `/api/telemetry`, `/api/logs/upload`, `/api/telemetry/live`, `/api/device/control/poll`, `/api/device/control/ack` |
| Vehicles | `routes/vehicles.py` | `/vehicles`, `/vehicles/<id>/scans`, `/vehicles/<id>/maintenance` |

Legacy routes `/analyze` and `/chat` exist in `app.py` for simulator/manual
features and are not part of ESP durable ingestion.

## Services

| Service | Responsibility |
| --- | --- |
| `services.provisioning` | Pairing code creation/validation, manual device creation, device token generation |
| `services.telemetry` | Durable upload validation, 29-byte RAW compatibility decode, session/record persistence, ACK generation, optional analyzer trigger |
| `services.analysis_submission` | Builds canonical telemetry from stored records, calls Analyzer HTTP API, stores `AnalysisResult` |
| `services.analysis` | Dashboard summaries, local statistical screening, chart/event payloads, compare payloads |
| `services.live` | Live sample validation, optional raw decode, Redis/in-memory latest-sample cache, SSE helpers, rate limiting |
| `services.live_control` | Live Mode command poll/ack lifecycle and browser command creation |
| `services.ml_client` | Analyzer health/status display |

## Data Model Map

Primary ownership chain:

```text
User
  -> Device
       -> RideSession
            -> TelemetryRecord
            -> SyncBatch
            -> RawArtifact
            -> AnalysisResult
```

Other tables:

- `DevicePairingCode`: one-time account-owned pairing secret; optionally links
  to the paired device after use.
- `EcuProfile` and `DecoderVersion`: created/updated from local Cloud profile
  definitions during upload.
- `DeviceControlRuntime` and `DeviceControlCommand`: Live Mode control state.
- `Vehicle`, `Scan`, `Maintenance`: older/manual vehicle feature set. Current
  durable upload does not bind sessions to a `Vehicle`; it uses
  `Device.vehicle_name` for display and canonical `vehicle_id` metadata.

## Auth Model

- Browser auth is Flask-Login session auth.
- Device auth is `X-Device-ID` plus `Authorization: Bearer <token>` in
  `routes/ingest.py:21`. The DB stores `sha256(token)`, not the clear token.
- Pairing uses a user-owned pairing code to create a `Device` and return a
  one-time token.
- Admin browser users can view all devices/sessions; regular users are scoped
  through `Device.user_id`.

## Frontend Data Access

The frontend mostly consumes server-rendered HTML plus:

- `static/js/dashboardApi.js`: sessions, samples, analysis, events, compare,
  devices, analysis rerun.
- `static/js/sessionDetail.js`: charts from `/api/sessions/<id>/samples`.
- `static/js/analysis.js`: manual analyzer rerun.
- `static/js/livePreview.js`: latest live sample, SSE stream, Live Mode control.
- `static/js/tool.js`: session playback and raw frame inspector.

Older scripts (`static/js/webSocket.js`, `liveData.js`, `settings.js`,
`vehicleInfo.js`, `script.js`) reference a `/ws` WebSocket, but the current
Flask app does not register a WebSocket route.

## Analyzer Integration Placement

Cloud does not import `ecuread-analyzer` Python modules, create RAW JSONL
files, or call analyzer subprocesses. It performs its own legacy 29-byte decode,
then sends canonical telemetry to the Analyzer HTTP endpoint
`POST /api/v1/analysis`.

