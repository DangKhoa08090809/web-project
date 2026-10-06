# ESP Node Overview

This audit treats the ESP32 firmware as the source of truth. The supplied Analyzer documents are comparison references only.

## Repository Map

| Area | Source of truth |
| --- | --- |
| Application entry point | `main/app_main.cpp` |
| ECU/K-Line module | `main/ecu/ecu_reader.cpp`, `main/ecu/honda_slow_init.hpp` |
| UART constants | `main/ecu/uart_settings.hpp`, `main/app_config.hpp`, `sdkconfig.defaults` |
| Honda Table 0x17 parser | `main/ecu/honda_protocol.cpp` |
| 24-byte to 29-byte compatibility mapping | `main/ecu/frame_mapping.cpp` |
| RAW diagnostic JSONL helper | `main/ecu/raw_capture_json.cpp` |
| Live local decoder | `main/ecu/live_decoder.cpp` |
| Road/session lifecycle | `main/offline/raw_road_lifecycle.cpp`, `main/app_main.cpp` |
| Binary raw storage | `main/storage/raw_session.cpp`, `main/storage/log_store.cpp` |
| Durable upload | `main/network/upload_worker.cpp`, `main/network/sync_client.cpp`, `main/network/sync_protocol.cpp` |
| Live upload/control | `main/network/live_preview.cpp`, `main/network/live_upload_worker.cpp`, `main/network/live_control_worker.cpp`, `main/network/live_control_protocol.cpp` |
| Provisioning/local portal | `main/network/provisioning_server.cpp`, `main/network/wifi_manager.cpp` |
| Host tests | `test/host/test_main.cpp`, `test/host/run_tests.ps1` |

## Runtime Shape

Normal boot mounts the raw telemetry partition, loads NVS credentials and counters, starts network workers, initializes K-Line, then polls the ECU in the main loop.

The active road logger uses `EcuReader::poll_raw_table17`, not the older decoded `poll(&sample)` path. It stores validated 24-byte Honda Table 0x17 frames in a compact binary raw session format. Production upload converts each stored 24-byte frame to a 29-byte backend compatibility frame by prepending five `0xFF` bytes.

## Active Contracts

| Contract | ESP behavior |
| --- | --- |
| Wire response consumed by firmware | 24-byte frame beginning `02 18 71 17` |
| Internal raw frame | 24 bytes |
| Local raw session record | 4-byte `elapsed_ms` plus 24-byte frame |
| Durable Cloud upload frame | 29-byte uppercase compact hex in `raw_frame` |
| Durable Cloud timestamp field | `device_time_ms`, derived from stored session-relative `elapsed_ms` |
| Live optional raw frame | 29-byte uppercase compact hex when raw frame diagnostics are enabled |
| Diagnostic JSONL helper | `device_time_ms`, `raw_hex`, `raw_length`, `checksum_ok`, using the supplied frame length |

## Important Non-Contracts

- No Pi5 implementation exists in this repository.
- No local MQTT, WebSocket, SSE, OTA, or `/api/device/commands` path exists.
- The active road logger does not store decoded sensor values.
- `main/telemetry/telemetry_store.cpp` is present but not built by `main/CMakeLists.txt`; do not treat it as active runtime behavior.
- The local `/api/logs/dump` route streams binary raw session bytes, not JSONL.
