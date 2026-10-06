# Pre-Road-Test Readiness Audit

Date: 2026-07-21

## Verdict

`NOT READY`

This checkout is not an ECU Reader firmware repository. It is the DriSafe Flask backend and dashboard for device provisioning, durable telemetry uploads, acknowledgements, analysis, and exports. The repository contains no ESP-IDF, Arduino, PlatformIO, or embedded firmware entry point, and no ECU UART/parser/storage implementation to build or flash.

Evidence:

- `README.md` states: "The firmware is out of scope for this repository. Device-to-server communication is HTTPS only."
- No `CMakeLists.txt`, `platformio.ini`, `sdkconfig*`, `partitions*.csv`, `.ino`, `.c`, `.cpp`, `.h`, or `.hpp` firmware files are present in the tracked repository.
- The configured Git remote is `https://github.com/DangKhoa08090809/web-project`, and `git ls-remote --heads --tags origin` reports only `refs/heads/master`.
- The GitHub API public repo list for `DangKhoa08090809` contains `dAIhe`, `n8n`, `web`, and `web-project`; none is a public ECU firmware repository.
- Direct probes for likely public repos `DangKhoa08090809/drisafe` and `DangKhoa08090809/ecu-reader` did not return readable public repositories.
- A local search found `/home/hthnh/ecuread-analyzer`, which is a FastAPI ECU log parser/analyzer with historical serial-log fixtures, not flashable device firmware.
- A local search found `/home/hthnh/TWaveTechLab/IoT_node/spool_scale_node`, which is ESP-IDF firmware for a filament spool scale, not the motorcycle ECU reader.
- There is no checked-in partition table, flash-size configuration, UART pin configuration, ECU baud rate configuration, watchdog setup, filesystem mount, local `/logs` writer, or boot entry point.

The backend can be tested and improved, but this repository alone cannot satisfy the acceptance criteria that the physical device boots, initializes ECU UART, mounts local storage, creates `/logs/session_*.csv`, records raw ECU frames while offline, survives reset, or can be flashed before the motorcycle test.

## Audited Scope

Covered in this repository:

- Flask app entry point: `app.py`
- Device provisioning/authentication: `routes/ingest.py`, `services/provisioning.py`
- Durable upload endpoint: `POST /api/logs/upload`
- Legacy single-record endpoint: `POST /api/telemetry`
- Live preview endpoint: `POST /api/telemetry/live`
- Telemetry validation and idempotent storage: `services/telemetry.py`
- Persistent backend storage schema: `models.py`, `migrations/versions/*`
- Dashboard/session export: `routes/dashboard.py`
- Existing tests: `tests/test_app.py`
- Backend configuration: `config.py`, `.env.example`, `docker-compose.yml`

Not present in this repository:

- Firmware boot code.
- ECU UART/CAN/K-line initialization.
- Frame boundary detection.
- Checksum implementation.
- Decoder implementation.
- Device-local filesystem mount.
- Device-local log writer.
- Device-local upload queue.
- Device reset/crash recovery.
- Watchdog/brownout handling.
- ESP partition table and flash-size config.

## Backend Data Path

The backend path is:

```text
HTTPS upload
-> device authentication
-> batch validation
-> duplicate-safe insert into ride_sessions/telemetry_records
-> gap-aware acknowledgement
-> optional dashboard analysis/export
```

The durable endpoint is offline-friendly from the device perspective: it accepts batches after connectivity returns and acknowledges only records that are present in backend storage. Device firmware must delete local records only after a successful `ok: true` response and only up to `accepted_sequences.contiguous_until`.

## Fixes Made During This Audit

- Durable uploads now require `raw_frame` to be a non-empty, even-length hexadecimal string or object metadata that includes `hex`, `raw_hex`, or `frame_hex`.
- Known metadata hex fields, `hex`, `raw_hex`, and `frame_hex`, are validated as non-empty, even-length hexadecimal strings.
- Malformed or byte-less raw-frame metadata is rejected before acknowledgement, preventing a device from deleting data the backend cannot recover.
- CSV session export now serializes `raw_frame` as JSON instead of Python object display text.
- Tests now cover raw-frame preservation, malformed raw-frame rejection, and JSON-recoverable CSV export.

## Backend Storage And Acknowledgement

Backend storage type: SQL database through SQLAlchemy.

Local device storage type: not present in this repository.

Backend durable record location:

```text
telemetry_records.raw_frame
telemetry_records.session_id
telemetry_records.seq
telemetry_records.device_time_ms
telemetry_records.timestamp
telemetry_records.server_received_at
```

Dashboard/export location:

```text
GET /sessions/<session_pk>.csv
GET /sessions/<session_pk>.json
```

Example durable upload record:

```json
{
  "seq": 1,
  "device_time_ms": 100,
  "rpm": 1500,
  "tps": 8.0,
  "ect": 80.0,
  "iat": 33.0,
  "battery": 13.7,
  "raw_frame": {
    "hex": "02187117",
    "frame_type": "sample",
    "checksum_ok": true,
    "valid": true
  }
}
```

Example acknowledgement:

```json
{
  "ok": true,
  "device_id": "xiao-a",
  "session_id": "session_boot_1_1",
  "received_count": 2,
  "inserted_count": 2,
  "duplicate_count": 0,
  "accepted_sequences": {
    "minimum": 1,
    "maximum": 2,
    "contiguous_until": 2
  },
  "server_time": "2026-07-21T00:00:00Z"
}
```

## Estimated Storage Consumption

This repository cannot estimate on-device filesystem consumption because no firmware log format, sample rate, or storage partition exists here.

For the backend upload format above, compact JSON is roughly 170 to 260 bytes per sample before HTTP overhead. At 10 samples/second, that is approximately 6.1 to 9.4 MB/hour of JSON payload. At 20 samples/second, that is approximately 12.2 to 18.7 MB/hour. A CSV firmware log with the same fields is likely smaller, but it must be measured against the actual firmware serializer and ECU sample rate.

## PowerShell Backend Verification Commands

Run backend tests:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -v
```

Run the backend locally:

```powershell
Copy-Item .env.example .env
.\.venv\Scripts\flask.exe --app app db upgrade
.\.venv\Scripts\flask.exe --app app run --debug
```

Send a manual durable upload after creating/registering a device and token:

```powershell
$BaseUrl = "http://127.0.0.1:5000"
$DeviceId = "xiao-a"
$DeviceToken = "replace-with-device-token"
$SessionId = "manual_$(Get-Date -Format yyyyMMdd_HHmmss)"
$Payload = @{
  session_id = $SessionId
  session_ended = $false
  records = @(
    @{
      seq = 1
      device_time_ms = 100
      rpm = 1500
      tps = 8.0
      ect = 80.0
      iat = 33.0
      battery = 13.7
      raw_frame = @{ hex = "02187117"; frame_type = "sample"; checksum_ok = $true; valid = $true }
    }
  )
} | ConvertTo-Json -Depth 6

Invoke-RestMethod `
  -Method Post `
  -Uri "$BaseUrl/api/logs/upload" `
  -Headers @{ "X-Device-ID" = $DeviceId; "Authorization" = "Bearer $DeviceToken" } `
  -ContentType "application/json" `
  -Body $Payload
```

## Firmware Commands

There is no firmware in this repository to build or flash. These commands are the expected ESP-IDF workflow once the actual XIAO ESP32S3 firmware repository is available:

```powershell
idf.py set-target esp32s3
idf.py fullclean
idf.py build
idf.py -p COM6 flash monitor
```

Erase flash only if provisioning or filesystem corruption requires it:

```powershell
idf.py -p COM6 erase-flash
idf.py -p COM6 flash monitor
```

The actual firmware repository must also provide commands to list and retrieve local logs, for example an ESP-IDF monitor command, USB mass-storage workflow, serial shell command, or documented LittleFS/SPIFFS extraction procedure. This backend repository cannot provide real on-device log retrieval commands.

## Required Firmware Acceptance Before Road Test

Do not connect the device for the road test until the firmware repository proves all of the following:

- Boot report prints reset reason, storage status, filesystem total/free bytes, session ID, log file path, ECU interface state, Wi-Fi/backend state, and recording state.
- Storage mounts at boot and creates a unique `/logs/session_*.csv` or equivalent JSONL/binary file.
- ECU receiving starts automatically without Wi-Fi/backend connectivity.
- Raw ECU bytes are written locally even when decode/checksum fails.
- Append failures and low-storage conditions are visible on serial.
- Flush policy is documented and tested.
- Reset does not overwrite previous sessions.
- Upload deletion is gated by backend `accepted_sequences.contiguous_until`.
- Firmware can be built cleanly and flashed with the exact board target and COM port.
- A host-side parser/logging simulation or hardware bench test writes at least one real or simulated raw frame to persistent storage.

## Two-Minute Pre-Test Checklist

1. Confirm you are using the actual firmware repository, not this backend checkout.
2. Run a clean firmware build and flash.
3. Open serial monitor and confirm the boot readiness banner.
4. Verify storage is `OK` and free space is printed.
5. Verify a new session log path is printed.
6. Connect ECU and wait for `first valid ECU frame`.
7. Confirm periodic status shows `logged` increasing and `dropped=0`.
8. Disconnect Wi-Fi or block backend access and confirm logging continues.
9. Reset the device and confirm the previous session is still listed/recoverable.
10. After the ride, export the newest local log before allowing destructive cleanup.
