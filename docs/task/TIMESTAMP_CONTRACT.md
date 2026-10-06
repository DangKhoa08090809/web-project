# Timestamp Contract

## Clocks Used

| Context | Field | Source clock | Unit | Width | Reset behavior |
| --- | --- | --- | --- | --- | --- |
| `RawEcuFrame` | `device_time_ms` | `esp_timer_get_time() / 1000` | ms | uint64 | Resets on boot |
| Binary raw record | `elapsed_ms` | Frame time minus session start frame time | ms | uint32 | Starts at 0 per raw session |
| Durable upload record | `device_time_ms` | Stored `elapsed_ms` | ms | uint32 serialized as number | Starts at 0 per raw upload session |
| Diagnostic JSONL helper | `device_time_ms` | Caller-supplied | ms | uint64 serialized as number | Depends on caller |
| Live sample | `device_time_ms` | Frame time minus live activation start | ms | uint64 field, uint32-derived | Starts at 0 per live activation |
| Live control poll | `uptime_ms` | Current monotonic boot time | ms | uint64 | Resets on boot |

## Sampling Point

For the active raw path, `RawEcuFrame.device_time_ms` is sampled after the complete UART frame is read and copied, before final Table 0x17 validation is returned to the caller.

The stored `elapsed_ms` is computed when appending a captured frame:

```text
elapsed_ms = frame.device_time_ms - session_start_ms
```

`session_start_ms` is the timestamp of the first pre-session frame included when the session opens.

## Wall Clock

SNTP can be started by Wi-Fi, and `util::utc_timestamp_ms` exists, but active raw session storage and upload do not use wall-clock timestamps.

## Overflow

- Boot monotonic frame times are uint64 in RAM.
- Stored raw session elapsed time is clamped to `UINT32_MAX`.
- Durable upload sends that uint32 elapsed value under the field name `device_time_ms`.

## Analyzer Implication

When analyzing production upload records, `device_time_ms` is not absolute boot time. It is session-relative elapsed time from the first stored pre-session frame.
