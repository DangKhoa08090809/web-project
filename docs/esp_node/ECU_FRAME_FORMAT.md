# ECU Frame Format

## Firmware-Proven Frame

The active stored frame is a 24-byte Honda Table 0x17 response.

```text
02 18 71 17 ... checksum
```

Validation is implemented by `validate_table17_frame` and active logging additionally requires `frame.length == 24`.

## 24 vs 29 Finding

| Stage | Length | Representation |
| --- | ---: | --- |
| UART response consumed as frame | 24 bytes expected for active logging | Starts at physical `0x02`; length byte is `0x18` |
| UART bytes before `0x02` | Variable/discarded | Not stored, not checksummed, not uploaded as part of node frame |
| `RawEcuFrame` in RAM | 24 bytes for valid active logging | `device_time_ms`, `checksum_ok`, `length`, `data[80]` |
| Pre-session buffer | 24 bytes | `CapturedFrame` |
| Binary raw storage | 24 bytes | `RawTelemetryRecord.frame` |
| Durable Cloud upload | 29 bytes | Five `FF` bytes prepended during upload conversion |
| Live optional raw upload | 29 bytes when enabled | Same compatibility conversion |
| Diagnostic JSONL helper | Caller-supplied length, normally 24 for ECU capture | Uses `raw_hex` |

## Five Leading `FF` Bytes

The five `FF` bytes are not added by the UART reader, not stored in raw sessions, and not part of the internal frame. They are added by `node_table17_to_backend_raw` in `main/ecu/frame_mapping.cpp` for backend compatibility.

If any bytes, including `FF`, arrive before the `0x02` start byte, `read_table17_response` discards them. That discard behavior does not prove those bytes are a logical ECU frame prefix.

## Byte Table, Active ESP Meaning

| Offset | Meaning known by ESP | Used for | Confidence |
| ---: | --- | --- | --- |
| 0 | Start byte `0x02` | Frame validation/start | EXACT_FROM_CODE |
| 1 | Declared length, active frame `0x18` | Read length and validation | EXACT_FROM_CODE |
| 2 | Service/response byte `0x71` | Header validation | EXACT_FROM_CODE |
| 3 | Table/id byte `0x17` | Header validation | EXACT_FROM_CODE |
| 4 | RPM high byte | Session start/stop, live decode | EXACT_FROM_CODE |
| 5 | RPM low byte | Session start/stop, live decode | EXACT_FROM_CODE |
| 6 | TPS voltage raw in live compatibility decoder | Live upload only | EXACT_FROM_CODE |
| 7 | TPS raw candidate in live compatibility decoder | Live upload only | EXACT_FROM_CODE |
| 8..9 | Stored and checksummed; no active road-logger meaning | RAW preservation | EXACT_FROM_CODE |
| 10 | Battery raw in live compatibility decoder | Live upload only | EXACT_FROM_CODE |
| 11 | IAT raw in live compatibility decoder | Live upload only | EXACT_FROM_CODE |
| 12 | ECT candidate raw in live compatibility decoder | Live upload only | EXACT_FROM_CODE |
| 13 | MAP raw candidate in live compatibility decoder | Live upload only | EXACT_FROM_CODE |
| 14..22 | Stored and checksummed; no active road-logger meaning | RAW preservation | EXACT_FROM_CODE |
| 23 | Checksum byte | Sum-zero validation | EXACT_FROM_CODE |

## Header Handling

- Active logging checks `02 18 71 17` at offsets 0..3 because it requires a 24-byte frame with start, length, service, and table id.
- `validate_table17_frame` can validate other declared lengths up to 80 bytes, but the active road logger refuses to log anything whose `RawEcuFrame.length` is not 24.
- No alternate Honda response header is accepted for active RAW storage.

## Compatibility Offset Mapping

The backend 29-byte compatibility frame has five prefix bytes, so backend index `b9` maps to node offset `4`. Live compatibility decoding uses:

| Backend index | Node offset | Live field |
| ---: | ---: | --- |
| 9 | 4 | RPM high |
| 10 | 5 | RPM low |
| 11 | 6 | TPS voltage |
| 12 | 7 | TPS raw candidate |
| 15 | 10 | Battery voltage |
| 16 | 11 | IAT |
| 17 | 12 | ECT candidate |
| 18 | 13 | MAP raw |

`parse_table17_frame` contains an older decoded-sample mapping, but the active road logger does not call that path.
