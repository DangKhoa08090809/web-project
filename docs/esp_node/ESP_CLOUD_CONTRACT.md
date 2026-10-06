# ESP Cloud Contract

All production POST requests use `Content-Type: application/json`. Authenticated requests also send:

```text
X-Device-ID: <device_id>
Authorization: Bearer <device_token>
```

The base URL is persisted in NVS and must be `https://...`; default is `https://drisafe.haithinh.top`.

## Endpoints

| Method | Endpoint | Trigger | Request | Expected response |
| --- | --- | --- | --- | --- |
| GET | `/` | Provisioning server reachability check | No auth | Any HTTP status `1..499` is considered reachable |
| POST | `/api/device/pair` | First provisioning after Wi-Fi test | `pairing_code`, `device_id`, `device_name`, `vehicle_name`, `ecu_type`, `firmware_version`, `hardware_version` | HTTP 201, JSON `ok:true`, `device.device_id` matches requested id, non-empty `device_token` |
| POST | `/api/logs/upload` | Upload worker processing oldest finalized session | `session_id`, `session_ended`, `batch_id`, `ecu_profile_id`, `records[]` | HTTP 200, JSON `ok:true`, matching `device_id`, matching `session_id`, numeric `accepted_sequences.contiguous_until` |
| POST | `/api/telemetry/live` | Live Mode upload worker | Live decoded sample, optional compatibility raw frame | Any 2xx plus JSON `ok:true`; if response includes `device_id`, it must match |
| POST | `/api/device/control/poll` | Paired control worker, default every 2000 ms | `boot_id`, `live_mode`, `firmware_version`, `uptime_ms` | HTTP 200, JSON `ok:true`, `command:null` or command object |
| POST | `/api/device/control/ack` | After applying/rejecting a command | `boot_id`, `command_id`, `status`, `live_mode` | HTTP 200, JSON `ok:true` |

## Upload Payload

Top-level fields:

```text
session_id      "<device_id>-raw-%06u"
session_ended   boolean
batch_id        "<session_id>-%04llu", based on first_seq / batch size
ecu_profile_id  "honda_keihin_legacy_29"
records         array
```

Record fields:

```text
seq             zero-based uint64 sequence within the raw session
device_time_ms  session-relative elapsed_ms from binary storage
raw_frame       29-byte uppercase compact hex compatibility frame
```

Default max batch size is 100 records. Payload construction caps at 96 KiB.

## Upload ACK And Retry

Local deletion requires:

- HTTP 200.
- JSON parses.
- `ok == true`.
- `device_id` matches persisted device id.
- `session_id` matches local upload session id.
- `accepted_sequences.contiguous_until` is numeric.
- The final batch had `session_ended == true`.
- `contiguous_until >= final_seq`.

On retry, the ESP reuses the same `session_id`, `seq`, and `raw_frame`. If a worker run restarts after a partial upload, it starts again from sequence 0 and relies on server duplicate handling.

Status handling:

| Status | ESP behavior |
| --- | --- |
| Network error | Retain local data, exponential backoff |
| 401/403 | Retain data, mark auth error, long retry |
| 413 | Retain data, reduce batch size when possible |
| 422 | Retain data, invalid batch retry delay |
| 429 | Retain data, bounded `Retry-After` |
| 5xx | Retain data, retry |
| Ambiguous response | Retain data |

## Local Portal Routes

These are local setup/AP routes, not Cloud endpoints:

```text
GET  /
GET  /api/status
GET  /api/wifi/networks
POST /api/provision
POST /api/live/configure
POST /api/reset
GET  /api/logs/status
GET  /api/logs/list
GET  /api/logs/dump?file=<session_000123.ecu>
POST /api/logs/delete?file=<session_000123.ecu>
```
