# ESP Handoff

## Hardware

- Target: Seeed Studio XIAO ESP32-C3, ESP-IDF target `esp32c3`.
- K-Line UART: UART1, GPIO21 TX, GPIO20 RX, 10400 baud, 8N1, no parity, no flow control.
- RX buffer: 1024 bytes. TX buffer: 0. UART event queue: 0.
- UART inversion is not configured.
- External L9637D/K-Line transceiver is assumed. No L9637D enable/wakeup GPIO is controlled by firmware.

```text
Honda ECU -> K-Line -> L9637D -> TX/RX UART -> ESP32-C3 UART1
```

## Initialization

Exact sequence:

1. Delete UART driver if installed.
2. Reset TX GPIO21 and set it as output.
3. Drive TX low for 70 ms.
4. Drive TX high for 120 ms.
5. Configure UART1 at 10400 8N1 on TX21/RX20.
6. Delay 25 ms.
7. Flush input.
8. Send `FE 04 72 8C`.
9. Wait TX done, max 100 ms.
10. Delay 60 ms.
11. Send `72 05 00 F0 99`.
12. Wait TX done, max 100 ms.
13. Delay 60 ms.
14. Flush input.
15. Normal polling sends `72 05 71 17 01`.

No fast-init path exists.

## Request

```text
72 05 71 17 01
```

The final byte is computed by `calc_checksum(72 05 71 17)`.

Each request flushes UART input before transmit and waits up to 100 ms for TX completion.

## Response

The active valid response is a 24-byte frame:

```text
02 18 71 17 ... checksum
```

Read behavior:

- Search for physical `0x02`.
- Discard bytes before `0x02`.
- Use byte 1 as declared frame length.
- Read exact declared length before the 180 ms deadline.
- Active storage accepts only 24-byte frames that validate header and checksum.

## Frame Representation

```text
ECU wire format consumed by ESP = 24 bytes starting 02 18 71 17
ESP internal format = 24-byte node Table 0x17 frame
ESP storage format = elapsed_ms + 24-byte node frame
ESP durable upload format = 29-byte legacy compatibility hex in raw_frame
```

The five leading `FF` bytes are generated during backend compatibility conversion, not stored internally.

## Checksum

```text
wire checksum rule = 24-byte sum(frame) % 256 == 0
compatibility checksum rule = 29-byte sum(frame) % 256 == 251
```

The 29-byte rule is implemented by prepending five `FF` bytes to a sum-zero 24-byte frame and then checking the 29-byte sum.

## Timestamp

- `RawEcuFrame.device_time_ms`: monotonic milliseconds since boot, sampled after full UART frame read.
- Stored raw record `elapsed_ms`: frame time minus session start frame time, uint32 clamped.
- Durable upload record `device_time_ms`: the stored session-relative `elapsed_ms`.
- Live upload `device_time_ms`: elapsed since live activation.

No wall-clock timestamp is used in active raw storage or upload.

## Continuous Acquisition

- Main poll interval: 250 ms.
- Response timeout: 180 ms.
- Re-init after 5 consecutive poll failures.
- Reconnect backoff: 1000 ms, doubled up to 30000 ms.
- Session opens after 3 consecutive valid non-zero RPM frames.
- Session closes after 10000 ms of zero RPM or no valid frame while logging.
- Active-session flash flush interval: 1500 ms.

## Pi Port

Port the K-Line behavior, request bytes, response validation, and 24-byte checksum. Reimplement GPIO/UART and timing on Linux. Treat Cloud upload and Live Mode as references, not as the K-Line hardware contract.

## Unknowns

- Exact L9637D board wiring and electrical levels are not proven by source.
- Hardware behavior cannot be fully verified without a live ECU/L9637D capture.
- Any bytes physically present before `0x02` are discarded by firmware, so this repo cannot prove their electrical origin.
- Analyzer/Cloud behavior beyond the ESP request/response expectations is outside this repository.
