# Live Mode Architecture

Current Cloud Live Mode is an ESP edition feature:

```text
browser command
  -> Cloud command row
  -> ESP control poll
  -> ESP live telemetry POSTs
  -> Redis/in-memory latest sample
  -> browser SSE stream
```

It is not part of durable session upload and should not be assumed to be Pi
local Live View.

## Device Control

Endpoints:

- `POST /api/device/control/poll`
- `POST /api/device/control/ack`
- Browser: `POST /api/devices/<device_id>/live/enable`
- Browser: `POST /api/devices/<device_id>/live/disable`
- Browser: `GET /api/devices/<device_id>/live/control`

State:

- `DeviceControlRuntime`: one row per device with `current_boot_id`,
  `last_control_poll_at`, `reported_live_mode`, firmware, uptime, and last
  command.
- `DeviceControlCommand`: command rows with `command_id`, `boot_id`, command
  type, status, delivery/ack timestamps, requester, and message.

Rules:

- A device is online for control only if its last poll is within
  `CONTROL_POLL_STALE_SECONDS`.
- Boot ID changes reset reported live mode and mark old active commands stale.
- Duplicate same-intent browser commands coalesce.
- New opposite intent supersedes older active commands.
- Ack is idempotent for terminal non-stale commands.

## Live Telemetry

Endpoint:

- `POST /api/telemetry/live`

Characteristics:

- Uses the same device-token auth as durable upload.
- Requires JSON, small body size, and per-device rate limit.
- Validates decoded sample fields.
- If optional `raw_frame` is supplied, decodes it with the same local
  `honda_keihin_legacy_29` Cloud decoder.
- Stores only the latest sample in Redis or in-memory backend.
- Adds `server_received_at` and `live_expires_at`.
- Does not update `Device.last_seen_at`.
- Does not create `RideSession`, `TelemetryRecord`, `SyncBatch`, or
  `AnalysisResult`.

## Browser Stream

Browser route:

- `GET /api/devices/<device_id>/live/stream`

Transport:

- Server-Sent Events, not WebSocket.
- Publishes `telemetry` events from the Redis/in-memory live backend.
- Sends heartbeat comments.
- Browser code is `static/js/livePreview.js`.

## Cleanup

Live sample cleanup is TTL-based:

- Redis keys expire after `LIVE_SAMPLE_TTL_SECONDS`.
- In-memory backend checks expiry on read.

Command rows persist in SQL; stale state is interpreted from poll age and boot
ID rather than deleted.

## Pi Implication

Pi local Live View should be treated as a separate local runtime concern. Cloud
Live Mode can remain for ESP edition. Pi Cloud MVP does not need to reuse
`/api/telemetry/live` unless a future product wants remote cloud live streaming
from Pi.

