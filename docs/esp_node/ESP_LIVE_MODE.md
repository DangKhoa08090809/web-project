# ESP Live Mode

## Summary

Live Mode is a temporary, best-effort Cloud-controlled preview path. It does not change ECU polling, request bytes, response validation, raw session storage, or durable upload behavior.

## Lifecycle

- Live state is disabled on every boot by `live.disable(monotonic_ms())`.
- Enabled/disabled state is RAM-only.
- Preferences are persisted: default duration, upload interval, and whether to include raw frame diagnostics.
- Default duration is 900 seconds and clamps to 60..3600 seconds.
- Default upload interval is 500 ms and clamps to 100..60000 ms.
- Live HTTP timeout is 5000 ms.

## Control Polling

The control worker starts only for paired devices and polls:

```text
POST /api/device/control/poll
```

Payload:

```json
{
  "boot_id": "boot_ecu-ab12ef_42_123456789abcdef0",
  "live_mode": false,
  "firmware_version": "1.0.0",
  "uptime_ms": 123456
}
```

Supported command types:

```text
ENABLE_LIVE_MODE
DISABLE_LIVE_MODE
```

The ESP requires command `boot_id` to equal the current runtime `boot_id`. Stale boot commands are rejected and can be acknowledged as `rejected`.

Ack payload:

```json
{
  "boot_id": "boot_ecu-ab12ef_42_123456789abcdef0",
  "command_id": "cmd_abc123",
  "status": "applied",
  "live_mode": true
}
```

Command application is idempotent. Repeated enable keeps Live ON without creating a new activation generation. Repeated disable keeps Live OFF.

## Sample Production

The main ECU polling loop offers a live sample only after a frame has passed active Table 0x17 validation. The live service stores only the newest pending sample; older unsent samples are overwritten.

Live sample `device_time_ms` is elapsed time since the current live activation started, not boot time and not raw road-session time.

## Upload Payload

Base fields:

```json
{
  "session_id": "ecu-ab12ef-live-42-0003",
  "seq": 0,
  "device_time_ms": 0,
  "ecu_profile_id": "honda_keihin_legacy_29",
  "rpm": 0,
  "tps_voltage": 0.488,
  "tps_raw_candidate": 0,
  "battery_voltage": 12.9,
  "iat_c": 33,
  "ect_c_candidate": 52,
  "map_raw": 89,
  "raw_length": 24,
  "parser_version": "honda-table17-node24-to-raw29-v1"
}
```

If raw frame diagnostics are enabled and the sample is a 24-byte node frame, the payload includes:

```json
{
  "raw_frame": "FFFFFFFFFF...",
  "raw_length": 29,
  "parser_version": "honda-table17-node24-to-raw29-v1"
}
```

Invalid out-of-range live fields are omitted. Live upload failures do not persist a backlog and do not affect raw road recording.

## Distinction From Pi5 Local Live View

ESP Live Mode is Cloud-controlled and disposable. A future Pi5 native local live view must not assume this is the same feature or reuse its session/timestamp semantics without review.
