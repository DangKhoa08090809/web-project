# DriSafe ECU Reader Server

Flask server for authenticated ECU telemetry from ESP/XIAO reader nodes. Each node is one owned `Device`; users manage devices in the dashboard, generate short-lived pairing codes, and devices upload offline logs over HTTPS with idempotent acknowledgements.

The firmware is out of scope for this repository. Device-to-server communication is HTTPS only.

## Dashboard Purpose

The public web dashboard is a historical session analysis and device-management surface. The motorcycle ECU data path is:

```text
Motorcycle ECU -> XIAO ESP32S3 -> local ride-session storage -> HTTPS upload when Internet is available -> backend storage -> analysis dashboard
```

The cloud dashboard is not a live driving dashboard. It reviews uploaded sessions, recent diagnostic results, decoded ECU charts, grouped anomaly events, comparison views, raw/sample inspection, and registered ECU reader devices. Live driving gauges, failure prediction, remaining-useful-life prediction, automatic definitive fault identification, firmware flashing, and ECU reverse-engineering changes are out of scope here.

Main navigation:

```text
Overview
Sessions
AI Analysis
Compare
Tool
Devices
Settings
```

`Tool` replaces the previous Classic Tool entry and works against uploaded sessions with historical playback.

## Architecture

- Flask application factory with Flask-Login, CSRF-protected browser forms, Flask-Migrate/Alembic, and SQLAlchemy models.
- PostgreSQL in production and SQLite for local development.
- Browser users own devices and ride sessions; admins can view and manage all devices and sessions.
- Pairing codes are short-lived, single-use, and stored only as hashes.
- Device API authentication uses `X-Device-ID` plus `Authorization: Bearer <device-token>`.
- Telemetry uniqueness is enforced by `(device database id, session_id, seq)`.
- Upload acknowledgements are computed from stored database rows after duplicate-safe inserts.
- Dashboard analysis helpers compute session statistics, downsample chart payloads, group consecutive abnormal samples into events, and keep diagnostic wording cautious.
- If embedded ML scores are present in stored raw-frame metadata, the dashboard uses them. Otherwise it labels results as local statistical screening; it does not claim an external Isolation Forest result when the ML API is unavailable.

DriveSafe Cloud owns RAW transport, persistence, and product workflow. Analyzer
owns ECU protocol interpretation. The authoritative production flow is:

```text
ESP32 / ECU
     |
     v
RAW ECU capture
     |
     +--> immutable raw artifact metadata and per-sample raw bytes
     |
     v
Cloud stores RAW unchanged
     |
     +--> Analyzer POST /api/v1/raw/decode-analyze
              |
              v
            canonical telemetry + analysis
     +--> realtime engineer monitor
     +--> session/history persistence
```

Downstream dashboard and live preview code must not reimplement byte positions
such as RPM/TPS/battery/IAT/ECT offsets. ESP durable RAW is decoded only by
Analyzer. Pi sync provides RAW24, canonical V2, and analysis produced before
Cloud import.

Ownership boundary:

```text
DriveSafe Cloud: auth, RAW transport/persistence, canonical persistence, sessions, frontend
Analyzer: RAW normalization, ECU decode, ML validation, windows, feature extraction, anomaly results
```

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set a stable `SECRET_KEY` in `.env`, then initialize and run:

```bash
flask --app app db upgrade
flask --app app create-user --admin
flask --app app run --debug
```

Open <http://127.0.0.1:5000>.

## Environment

Important variables:

```dotenv
APP_ENV=development
SECRET_KEY=replace-with-a-long-random-value
DATABASE_URL=
MAX_CONTENT_LENGTH=2097152
MAX_LOG_RECORDS=1000
RAW_FRAME_MAX_BYTES=16384
REDIS_URL=
LIVE_REDIS_REQUIRED=
LIVE_SAMPLE_TTL_SECONDS=15
LIVE_RATE_LIMIT_PER_SECOND=5
LIVE_RATE_LIMIT_BURST=10
LIVE_MAX_REQUEST_BYTES=4096
LIVE_SSE_HEARTBEAT_SECONDS=15
LIVE_RAW_FRAME_MAX_CHARS=256
CONTROL_POLL_STALE_SECONDS=10
ML_API_BASE_URL=
ML_API_TIMEOUT_SECONDS=2.5
ML_MODEL_NAME=Isolation Forest
ML_MODEL_VERSION=
ML_TRAINING_DATA_VERSION=
ML_FEATURE_SCHEMA_VERSION=
ML_DETECTION_THRESHOLD=
PAIRING_CODE_TTL_SECONDS=900
PAIRING_MAX_ATTEMPTS=10
SESSION_COOKIE_SECURE=false
TRUST_PROXY=false
TRUSTED_HOSTS=
```

When `APP_ENV=production`, `SECRET_KEY` and PostgreSQL settings are required. Production defaults also enable secure cookies, HTTPS URL generation, and proxy header support.

`ML_API_BASE_URL` is optional. When it is empty, the AI Analysis page reports `ML API unavailable` and re-analysis requests return a failed state with a clear configuration message. When it is set, the dashboard probes `<ML_API_BASE_URL>/health` for availability and submits completed ESP RAW sessions to `<ML_API_BASE_URL>/api/v1/raw/decode-analyze`. Analyzer failure never rolls back captured RAW telemetry; sessions keep separate `capture_status` and `analysis_status` values so they can be retried later.

## Database Migrations

Do not use `db.create_all()` for production.

```bash
flask --app app db upgrade
# After model changes:
flask --app app db migrate -m "describe change"
flask --app app db upgrade
```

The current schema includes users, devices, pairing codes, ECU profiles, decoder versions, ride sessions, telemetry records, raw artifact metadata, lightweight analysis results, and sync batch diagnostics.

## Users

Create the first admin from a trusted shell:

```bash
flask --app app create-user --email admin@example.com --name "DriSafe Admin" --admin
```

Passwords are hashed with Werkzeug. Login failures use the same message for unknown accounts and bad passwords.

## Pairing A Node

1. Log in to `https://drisafe.haithinh.top`.
2. Open Devices.
3. Generate a pairing code.
4. Enter that code into the ESP configuration portal.
5. The node calls the pairing API and stores the returned device token.

Pairing request:

```bash
curl -X POST "https://drisafe.haithinh.top/api/device/pair" \
  -H "Content-Type: application/json" \
  -d '{
    "pairing_code": "ABCD-EFGH-JKLM-NPQR",
    "device_id": "ecu-abc123",
    "device_name": "ECU Reader",
    "vehicle_name": "Test Motorcycle",
    "ecu_type": "Unknown ECU",
    "firmware_version": "1.0.0",
    "hardware_version": "xiao-esp32"
  }'
```

Success returns `Cache-Control: no-store` and the permanent token exactly once:

```json
{
  "ok": true,
  "device": {
    "device_id": "ecu-abc123",
    "device_name": "ECU Reader"
  },
  "device_token": "one-time-visible-secret"
}
```

The database stores only token and pairing-code hashes.

## Device Authentication

All device telemetry endpoints require:

```text
X-Device-ID: ecu-abc123
Authorization: Bearer DEVICE_TOKEN
```

Inactive devices are rejected immediately. Token rotation is available from the Devices page or:

```bash
curl -X POST "https://drisafe.haithinh.top/api/devices/DEVICE_DATABASE_ID/rotate-token" \
  -b cookies.txt \
  -H "X-CSRFToken: CSRF_TOKEN"
```

## Live Preview

Live Preview is a near-realtime HTTPS diagnostics path for checking current sensor values, parser output, and raw bytes. It is not the main cloud dashboard flow and is not used as a live driving dashboard. Live Preview data may be dropped and is not part of the durable ride dataset. It is not normal operational telemetry, ride history, research data, training data, or a persistent session log.

The ESP node must continue using `/api/logs/upload` for persistent telemetry and acknowledgement-based deletion. Live Preview never returns `accepted_sequences`, durable row ids, or token details.

Production Live Preview state uses Redis for latest-value cache, TTL expiry, rate limiting, and Pub/Sub fan-out to browser Server-Sent Events. Redis is internal-only in Docker Compose and is not published on a host port. Local development can omit `REDIS_URL`; the server will use a process-local memory fallback, which is only suitable for one-process development because Gunicorn workers do not share it.

Device ingest:

```text
POST /api/telemetry/live
Content-Type: application/json
X-Device-ID: <device-id>
Authorization: Bearer <device-token>
```

Request body fields are a single latest sample. Required fields are `session_id`, `seq`, and `device_time_ms`. Optional compatibility fields are `timestamp`, `rpm`, `tps`, `tps_voltage`, `ect`, `iat`, `battery`, `injector_ms`, `injector_raw`, `fuel_cut_inferred`, `raw_frame`, `raw_length`, `parser_version`, `ignition_deg`, and `ecu_profile_id`. If `raw_frame` contains a supported ECU frame, DriveSafe decodes it once and publishes the canonical sample to the live monitor. Unknown fields are rejected. Numeric fields must be finite, booleans must be real JSON booleans, `raw_frame` must be an even-length hex string, and live request bodies are capped by `LIVE_MAX_REQUEST_BYTES`.

Example:

```bash
curl -X POST "https://drisafe.haithinh.top/api/telemetry/live" \
  -H "Content-Type: application/json" \
  -H "X-Device-ID: ecu-test-001" \
  -H "Authorization: Bearer DEVICE_TOKEN" \
  -d '{
    "session_id": "ecu-test-001-boot-1",
    "seq": 100,
    "device_time_ms": 25000,
    "rpm": 1800,
    "tps": 9.5,
    "ect": 81,
    "iat": 34,
    "battery": 13.8,
    "injector_ms": 2.3,
    "fuel_cut_inferred": false,
    "raw_frame": "02187117"
  }'
```

Success response:

```json
{
  "ok": true,
  "device_id": "ecu-test-001",
  "server_received_at": "2026-07-14T10:00:00.000Z"
}
```

Browser endpoints require normal login cookies and enforce device ownership; admins can access all devices:

```text
GET /devices/<device_id>/live
GET /api/devices/<device_id>/live/latest
GET /api/devices/<device_id>/live/stream
```

`/api/devices/<device_id>/live/latest` returns `Cache-Control: no-store` and either the current sample or `{ "online": false, "sample": null }`. `/api/devices/<device_id>/live/stream` is an SSE stream with `telemetry` events and heartbeat comments. The stream uses `Cache-Control: no-cache, no-store` and `X-Accel-Buffering: no`.

Default live behavior:

```text
TTL: 15 seconds
Sustained device rate: 5 requests/second
Burst: 10 requests
Overload response: HTTP 429 with Retry-After
Raw frame cap: 256 hex characters
Redis keys: drisafe:live:<device_id>:latest and drisafe:live:<device_id>:channel
```

Troubleshooting:

- `503 LIVE_PREVIEW_UNAVAILABLE`: Redis is unavailable or `REDIS_URL` is missing while Redis is required.
- `429 LIVE_RATE_LIMITED`: the device exceeded the live token-bucket rate limit; durable `/api/logs/upload` is unaffected.
- Browser shows `DISCONNECTED`: check the reverse proxy SSE buffering settings and that the stream response is not being cached.
- Browser shows `NO DATA`: no live sample exists or the latest sample TTL expired.

## Remote Live Mode Control

Live Mode control is HTTP polling from the node. It only controls the runtime-only Live Preview producer on the ECU Reader; it does not change durable upload behavior and it does not persist a desired Live Mode state across reboots.

The node generates a fresh `boot_id` on every boot and starts with Live Mode off. Server commands are targeted to the current `(device_id, boot_id)`. A command for boot A is never returned to boot B.

Device poll:

```text
POST /api/device/control/poll
Content-Type: application/json
X-Device-ID: <device-id>
Authorization: Bearer <device-token>
```

```json
{
  "boot_id": "boot_123",
  "live_mode": false,
  "firmware_version": "1.0.0",
  "uptime_ms": 2500
}
```

When no command is pending:

```json
{
  "ok": true,
  "command": null,
  "server_time": "2026-08-26T12:00:00Z"
}
```

When a command is pending:

```json
{
  "ok": true,
  "command": {
    "command_id": "cmd_abc123",
    "boot_id": "boot_123",
    "type": "ENABLE_LIVE_MODE"
  },
  "server_time": "2026-08-26T12:00:00Z"
}
```

Device acknowledgement:

```text
POST /api/device/control/ack
Content-Type: application/json
X-Device-ID: <device-id>
Authorization: Bearer <device-token>
```

```json
{
  "boot_id": "boot_123",
  "command_id": "cmd_abc123",
  "status": "applied",
  "live_mode": true
}
```

Supported command types are `ENABLE_LIVE_MODE` and `DISABLE_LIVE_MODE`. Acknowledgement statuses are `applied`, `rejected`, and `failed`.

Browser control endpoints require normal dashboard login, device ownership or admin access, and CSRF protection:

```text
GET  /api/devices/<device_id>/live/control
POST /api/devices/<device_id>/live/enable
POST /api/devices/<device_id>/live/disable
```

The dashboard considers control online only when the node has polled within `CONTROL_POLL_STALE_SECONDS` seconds. If no fresh runtime exists, enable/disable returns `409 DEVICE_OFFLINE` and no indefinite command is queued. Duplicate clicks coalesce with an existing pending/delivered command for the same boot and type. If Enable is pending and Disable is requested, the older command is superseded and the newest intent wins.

The Live Preview page shows `OFF`, `ENABLING...`, `ON`, `DISABLING...`, or unavailable. It reports Live Mode as on only from node acknowledgement or node-reported runtime state; a user click by itself is only pending.

## Uploading Telemetry

Single development record:

```bash
curl -X POST "https://drisafe.haithinh.top/api/telemetry" \
  -H "Content-Type: application/json" \
  -H "X-Device-ID: ecu-abc123" \
  -H "Authorization: Bearer DEVICE_TOKEN" \
  -d '{
    "session_id": "ecu-abc123-boot-42-a81f",
    "seq": 1,
    "device_time_ms": 100,
    "rpm": 1500,
    "tps": 8,
    "ect": 80,
    "iat": 33,
    "battery": 13.7
  }'
```

Offline batch:

```bash
curl -X POST "https://drisafe.haithinh.top/api/logs/upload" \
  -H "Content-Type: application/json" \
  -H "X-Device-ID: ecu-abc123" \
  -H "Authorization: Bearer DEVICE_TOKEN" \
  -d '{
    "session_id": "ecu-abc123-boot-42-a81f",
    "session_ended": false,
    "records": [
      {
        "seq": 1,
        "device_time_ms": 100,
        "rpm": 1500,
        "tps": 8,
        "ect": 80,
        "iat": 33,
        "battery": 13.7
      }
    ]
  }'
```

Timestamps are optional and must be timezone-aware ISO 8601 or Unix epoch seconds when present. `device_time_ms` can be used when the node does not know wall-clock time. The server always records `server_received_at`.

Durable uploads must preserve the raw ECU bytes whenever they are available. `raw_frame` may be either a non-empty, even-length compact hexadecimal string such as `"02187117"`, a separated byte string such as `"FF;FF;02;18"`, or an object containing parser metadata plus one of `hex`, `raw_hex`, or `frame_hex`. Those hex fields must also contain recoverable raw bytes. Malformed or byte-less raw-frame metadata is rejected so a node does not receive an acknowledgement for data the backend cannot recover later. CSV exports serialize `raw_frame` as JSON so both string and object forms can be parsed back exactly.

When a supported raw ECU frame is present, uploaded decoded numeric fields are treated as compatibility hints only. DriveSafe parses the frame, checks the checksum rule, applies the persisted ECU profile/decoder version, stores canonical telemetry fields, and fans that same decoded sample out to session history and analyzer submission. If the analyzer is unavailable, the captured session remains stored with `analysis_status=failed` or `not_requested` and can be retried from the dashboard.

## Acknowledgement Semantics

Successful uploads return:

```json
{
  "ok": true,
  "device_id": "ecu-abc123",
  "session_id": "ecu-abc123-boot-42-a81f",
  "received_count": 100,
  "inserted_count": 93,
  "duplicate_count": 7,
  "accepted_sequences": {
    "minimum": 1001,
    "maximum": 1100,
    "contiguous_until": 1100
  },
  "server_time": "2026-07-13T10:00:00Z"
}
```

`accepted_sequences.contiguous_until` is the highest stored sequence number for which every record from the session's stored minimum sequence through that value exists on the server. It is not simply the largest received sequence.

Example: if the server has `1, 2, 3, 5`, then `contiguous_until` is `3`.

The node must delete local telemetry only after receiving a successful response and only up to `accepted_sequences.contiguous_until`.

## Docker

Copy `.env.example` to `.env`, replace every secret, then run:

```bash
docker compose up --build
docker compose exec web flask --app app create-user --admin
```

The web container listens internally on port `5000`, applies migrations, and starts Gunicorn with the `gthread` worker class. The default `2` workers and `8` threads keep long-lived SSE streams from consuming the only available worker. PostgreSQL and Redis are not published publicly. Redis is used as an ephemeral cache and Pub/Sub bus; persistence is disabled for Live Preview state. The compose file exposes only the web service to the external `npm-proxy` network for the existing reverse proxy.

## Production: drisafe.haithinh.top

Recommended production settings:

```dotenv
APP_ENV=production
SESSION_COOKIE_SECURE=true
TRUST_PROXY=true
TRUSTED_HOSTS=drisafe.haithinh.top
MAX_CONTENT_LENGTH=2097152
MAX_LOG_RECORDS=1000
REDIS_URL=redis://redis:6379/0
LIVE_REDIS_REQUIRED=true
LIVE_SAMPLE_TTL_SECONDS=15
LIVE_RATE_LIMIT_PER_SECOND=5
LIVE_RATE_LIMIT_BURST=10
LIVE_MAX_REQUEST_BYTES=4096
LIVE_SSE_HEARTBEAT_SECONDS=15
LIVE_RAW_FRAME_MAX_CHARS=256
CONTROL_POLL_STALE_SECONDS=10
PAIRING_CODE_TTL_SECONDS=900
PAIRING_MAX_ATTEMPTS=10
GUNICORN_WORKERS=2
GUNICORN_THREADS=8
```

Nginx or Nginx Proxy Manager should terminate HTTPS for `drisafe.haithinh.top` and proxy to `drisafe-web:5000`. The app uses `ProxyFix` when `TRUST_PROXY=true`.

For the current Nginx Proxy Manager and Cloudflare Tunnel topology, keep the Cloudflare tunnel pointed at NPM on local HTTP port `80`, then configure the NPM proxy host:

```text
Domain Names: drisafe.haithinh.top
Scheme: http
Forward Hostname / IP: drisafe-web
Forward Port: 5000
Websockets Support: enabled
Cache Assets: disabled
Force SSL: disabled when Cloudflare already supplies the browser-facing HTTPS scheme
```

The `web` compose service joins the external `npm-proxy` Docker network so NPM can resolve `drisafe-web`. Paste `deploy/npm-advanced.conf` into the NPM Advanced field for this host when using the optional device Live Preview SSE endpoint.

For SSE, the reverse proxy location for `/api/devices/<device_id>/live/stream` must disable buffering and caching and use a long read timeout:

```nginx
proxy_buffering off;
proxy_cache off;
proxy_read_timeout 3600s;
add_header X-Accel-Buffering no;
```

When Cloudflare is in front of Nginx, keep Live Preview as normal HTTPS traffic and avoid proxy rules that buffer or transform `text/event-stream` responses.

Health check:

```bash
curl https://drisafe.haithinh.top/health
```

Expected:

```json
{"status":"ok","database":"ok","redis":"ok"}
```

Local memory fallback reports `"redis":"memory"`. Production reports unhealthy if Redis is unavailable or not configured.

## Tests

```bash
.venv/bin/python -m unittest discover -v
```

The tests use an isolated in-memory SQLite database and an in-memory Live Preview backend. They cover login safety, ownership checks, pairing, token rotation, device authentication, idempotent uploads, gap-aware acknowledgements, live validation, rate limiting, latest sample responses, SSE filtering and heartbeat behavior, canonical ECU decode regression, decoder provenance persistence, raw artifact metadata, analyzer failure safety, analyzer retry behavior, and the guarantee that live samples do not create durable telemetry rows or ride sessions.

Dashboard coverage includes overview summaries, recent diagnoses, session filtering, session statistics, anomaly event grouping, large-session downsampling, AI API unavailable handling, re-analysis state responses, compare metrics, Tool playback page rendering, and unit-formatting helpers.

## Dashboard Development

Local development:

```bash
source .venv/bin/activate
flask --app app db upgrade
flask --app app run --debug
```

Production uses the Docker workflow described below. Run migrations before serving a new deployment:

```bash
flask --app app db upgrade
docker compose up --build
```

No mock-data mode is enabled by default. Use real `/api/logs/upload` session data for production-like validation; if mock data is added later, it must be visibly labeled as simulated and structurally match the real API responses.
