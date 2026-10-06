# Honda ECU Request Response

## Normal Poll Cycle

The active loop is in `main/app_main.cpp` and uses `EcuReader::poll_raw_table17`.

```text
every 250 ms:
  flush UART input
  send 72 05 71 17 01
  wait for TX complete, max 100 ms
  read until 0x02 start byte, discarding earlier bytes
  read length byte
  read exactly declared length before the 180 ms deadline
  validate 24-byte Table 0x17 frame before storage/live use
  update lifecycle, storage, and live preview
```

## Request

| Field | Value |
| --- | --- |
| Request bytes | `72 05 71 17 01` |
| Length | 5 bytes |
| Checksum construction | `calc_checksum({72 05 71 17}) == 01` |
| Sent by | `EcuReader::send_table17_request` |
| UART input before send | Flushed |
| TX wait | `uart_wait_tx_done(..., 100 ms)` |

## Response Read Strategy

| Behavior | ESP implementation |
| --- | --- |
| Frame start detection | Read one byte at a time until `0x02` |
| Leading bytes before `0x02` | Discarded and counted in `discarded_uart_bytes` |
| Length source | Second byte of response |
| Minimum response length | 24 bytes |
| Maximum read capacity | `config::kRawFrameMax`, 80 bytes |
| Read deadline | One total 180 ms deadline for start, length, and payload |
| Partial read | Returns timeout |
| Impossible length | Returns invalid frame |
| Extra bytes after declared frame | Not part of current frame; next request flushes UART input |
| Per-request retry | None |
| Re-init threshold | 5 consecutive poll failures |
| Re-init backoff | 1000 ms min, doubled to 30000 ms max |

## Polling And Session Timing

| Item | Value |
| --- | ---: |
| Main ECU poll interval | 250 ms |
| Session start confirmation | 3 consecutive valid non-zero RPM frames |
| Session close timeout | 10000 ms of zero RPM or no valid frame while logging |
| Active-session flash flush interval | 1500 ms |

`CONFIG_DRISAFE_ENGINE_STOP_DEBOUNCE_MS` exists with default 5000 ms, but the active raw road logger is constructed with `CONFIG_DRISAFE_SESSION_END_TIMEOUT_MS` and closes after that timeout. The code path does not use `kEngineStopDebounceMs`.

## Commands

Only Honda Table 0x17 is polled by the active road logger. The wakeup/init frames are sent during reconnect/init only; they are not regular poll commands.
