# Checksum Contract

## ECU Wire Checksum

```text
Frame length: 24 bytes for active Table 0x17 logging
Rule: sum(frame) % 256 == 0
Checksum byte: offset 23 in the 24-byte frame
```

`verify_checksum` sums every byte into an 8-bit accumulator and requires the result to be zero.

`calc_checksum(data, len)` returns the two's-complement checksum byte for a prefix by subtracting each byte from an 8-bit accumulator.

## Request Checksum

The Table 0x17 request prefix is:

```text
72 05 71 17
```

The firmware computes:

```text
calc_checksum(72 05 71 17) == 01
```

So the transmitted request is:

```text
72 05 71 17 01
```

## Firmware Response Validation

1. Read response from UART starting at `0x02`.
2. Use byte 1 as declared length.
3. Validate start byte, declared length, service/table header, and checksum.
4. Active logging stores only exact 24-byte frames that pass validation.

The response validator does not separately compare offset 23 to `calc_checksum(frame[:23])`, but the sum-zero check is equivalent for the whole frame.

## Backend Compatibility Checksum

The backend compatibility frame is:

```text
FF FF FF FF FF + 24-byte node frame
```

`backend_raw_checksum_ok` requires:

```text
len(frame) == 29
sum(frame) % 256 == 251
```

This is an implementation fact in `frame_mapping.cpp`: `node_table17_to_backend_raw` prepends five `0xFF` bytes and then requires `backend_raw_checksum_ok`.

Because the stored node frame is sum-zero and `5 * 0xFF == 251 mod 256`, the generated 29-byte frame has sum `251`. This is not merely inferred math; it is enforced by the conversion function before upload hex is produced.

## Final Statement

```text
ECU checksum contract:
24-byte Table 0x17 frame, sum(frame) % 256 == 0, checksum byte at node offset 23.

ESP compatibility checksum representation:
29-byte backend frame, five generated FF prefix bytes plus the 24-byte node frame,
sum(frame) % 256 == 251.
```

The five generated `FF` bytes participate only in the backend compatibility checksum, not in the ECU wire checksum used by the ESP parser.
