# ECU Master Dashboard

A Flask foundation for authenticated ECU telemetry from ESP/XIAO readers. It supports PostgreSQL in production, SQLite for local development, browser authentication, hashed device tokens, live HTTP ingestion, idempotent offline uploads, ride sessions, and chart-ready session data.

## Local setup

Python 3.11+ is recommended.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                 # Windows: copy .env.example .env
```

Replace `SECRET_KEY` in `.env` (for example, generate one with `python -c "import secrets; print(secrets.token_hex(32))"`). Leave `DATABASE_URL` commented to use SQLite, then initialize and run:

```bash
flask --app app db upgrade
flask --app app create-user --admin
flask --app app run --debug
```

Open <http://127.0.0.1:5000>. The dashboard, device and session pages require login. Existing simulator/analyze, maintenance, error, vehicle, and settings pages remain under **Classic tools** and are protected too.

To generate development telemetry, put a registered `DEVICE_ID` and its one-time `DEVICE_TOKEN` in `.env`, then run `python simu.py`.

Do not use `db.create_all()` for normal setup. Schema changes should use:

```bash
flask --app app db migrate -m "describe change"
flask --app app db upgrade
```

## Users and devices

Create users from the trusted server console. Passwords must have at least 12 characters and are stored with Werkzeug's password hash:

```bash
flask --app app create-user --email admin@example.com --name "ECU Admin" --admin
```

After login, use **Devices → Register a reader**. The device token is displayed once; only its SHA-256 hash is stored. Tokens are high-entropy random credentials. A normal user sees only their own devices; an admin can view all users' devices.

The same flow is available via JSON. This complete curl example obtains a CSRF token, logs in, and creates a device (requires `jq`):

```bash
CSRF=$(curl -s -c cookies.txt http://localhost:5000/auth/csrf | jq -r .csrf_token)
LOGIN=$(curl -s -b cookies.txt -c cookies.txt \
  -H "Content-Type: application/json" -H "X-CSRFToken: $CSRF" \
  -d '{"email":"admin@example.com","password":"your-long-password"}' \
  http://localhost:5000/auth/login)
CSRF=$(printf '%s' "$LOGIN" | jq -r .csrf_token)

curl -s -b cookies.txt \
  -H "Content-Type: application/json" -H "X-CSRFToken: $CSRF" \
  -d '{"device_id":"xiao-ecu-001","device_name":"Garage reader","vehicle_name":"Civic EK","ecu_type":"OBD-II CAN"}' \
  http://localhost:5000/api/devices
```

Copy the returned `device.token` immediately. It is never returned by later GET requests.

## ESP/XIAO HTTP ingestion

Every device request requires both headers:

```text
X-Device-ID: xiao-ecu-001
Authorization: Bearer <device_token>
```

Send one live record:

```bash
curl -X POST http://localhost:5000/api/telemetry \
  -H "Content-Type: application/json" \
  -H "X-Device-ID: xiao-ecu-001" \
  -H "Authorization: Bearer $DEVICE_TOKEN" \
  -d '{
    "session_id":"ride-2026-07-06-001",
    "seq":1,
    "timestamp":"2026-07-06T14:01:02.125Z",
    "rpm":2450,"tps":18.5,"ect":88.2,"iat":32.1,"battery":13.9,
    "injector_ms":3.4,"ignition_deg":16.0,"raw_frame":"7E8 08 04 41 0C 26 48"
  }'
```

Success is HTTP `201` with `{"inserted":1,"skipped":0,...}`. A retry of the same `(device_id, session_id, seq)` is safe and returns an inserted count of zero. Valid traffic updates the device's `last_seen_at`.

Upload an offline batch:

```bash
curl -X POST http://localhost:5000/api/logs/upload \
  -H "Content-Type: application/json" \
  -H "X-Device-ID: xiao-ecu-001" \
  -H "Authorization: Bearer $DEVICE_TOKEN" \
  -d '{
    "session_id":"ride-2026-07-06-002",
    "records":[
      {"seq":1,"timestamp":"2026-07-06T15:00:00Z","rpm":1200,"tps":4.2,"ect":80,"battery":14.1},
      {"seq":2,"timestamp":"2026-07-06T15:00:01Z","rpm":1450,"tps":6.1,"ect":81,"battery":14.0}
    ]
  }'
```

The response reports `inserted` and `skipped`. Batches are limited by `MAX_LOG_RECORDS` (default 1,000) and the entire HTTP body by `MAX_CONTENT_LENGTH` (default 2 MiB). Records in one upload must share its top-level session ID. Timestamps accept timezone-aware ISO 8601 strings or Unix epoch seconds.

## PostgreSQL and Docker

For local PostgreSQL, set:

```dotenv
DATABASE_URL=postgresql+psycopg://ecu_user:password@localhost:5432/ecu_dashboard
```

For containers, copy `.env.example` to `.env`, replace every placeholder secret, then run:

```bash
docker compose up --build
docker compose exec web flask --app app create-user --admin
```

The web container applies migrations before Gunicorn starts. In a multi-replica deployment, run migrations as a separate one-off release job instead. Set `SESSION_COOKIE_SECURE=true` behind HTTPS.

The optional development Mosquitto placeholder starts with:

```bash
docker compose --profile mqtt up --build
```

It permits anonymous local connections and must not be exposed as-is.

Run the backend regression suite with `python -m unittest discover -v`.

## MQTT-ready contract

`mqtt_service.py` defines the transport seam and these future topics:

- `ecu/<device_id>/telemetry` — one object with the same fields as `POST /api/telemetry`
- `ecu/<device_id>/status` — `{ "online": true, "timestamp": "...", "firmware": "..." }`
- `ecu/<device_id>/logs` — the same `{ "session_id": "...", "records": [...] }` object as offline upload

The future subscriber should authenticate devices at the broker, derive `device_id` from the topic, decode JSON, and call `services.telemetry.validate_record` / `validate_batch` and `store_records`. This ensures MQTT and HTTP enforce the same validation and duplicate policy.

## Production notes

- Set `APP_ENV=production`, a stable random `SECRET_KEY`, PostgreSQL `DATABASE_URL`, HTTPS, and `SESSION_COOKIE_SECURE=true`.
- Device token rotation is available at `POST /api/devices/<database-id>/rotate-token` to the owning user (or an admin). It invalidates the prior token immediately.
- Back up PostgreSQL, add rate limiting at the reverse proxy, and configure broker TLS/passwords before enabling MQTT externally.
- The in-memory classic simulator state is intentionally compatibility-only; durable ECU telemetry belongs in the ingestion APIs.
