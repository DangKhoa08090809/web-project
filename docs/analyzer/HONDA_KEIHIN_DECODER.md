# Honda/Keihin Decoder Contract

This repository contains two Honda/Keihin-related decoder paths. They must not
be conflated.

1. Current runtime RAW compatibility decoder:
   `app.ingestion.raw_ecu.decoder`, exposed through `app.ecu.decoder`.
2. Audited `0x71/0x17` profile decoder:
   `app.ecu.profiles.honda_keihin_71_17`.

Confidence labels in this document are restricted to:

- `VERIFIED`: code plus tests/calibration/evidence in this repository support the mapping.
- `PROVISIONAL`: implemented or high-confidence, but not fully verified.
- `UNKNOWN`: source bytes may be preserved, but physical identity/scaling is not proven or is contradicted.

## Current Runtime Decoder: `honda_keihin_legacy_29:0.1.0`

Runtime provenance constants:

| Field | Value |
| --- | --- |
| `ecu_profile_id` | `honda_keihin_legacy_29` |
| `decoder_id` | `honda_keihin_legacy_29` |
| `decoder_version` | `0.1.0` |
| Input frame | 29 bytes |
| Legacy prefix seen in fixtures | `FF FF FF FF FF` |
| Checksum | `sum(frame) % 256 == 251` |

Important behavior:

- `decode_frame` requires exactly 29 bytes.
- The legacy runtime decoder does not enforce the `02 18 71 17` header itself.
  Header/profile validation exists in the audited 24-byte profile module.
- Parsed frames with invalid checksums can still be decoded into rows, but
  `ml_eligible` requires both checksum validity and decoded signal validation.
- `validate_decoded_signals` checks configured physical ranges, not ECU profile
  identity.

### Runtime Signal Table

Byte indexes below are zero-based indexes in the 29-byte legacy frame.

| Signal | Source byte(s) | Endian | Formula | Unit | Evidence | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| `rpm` | `b[9]`, `b[10]` | big-endian u16 | `(b[9] << 8) | b[10]` | rpm | Decoder code; `tests/test_decoder.py`; calibration report verifies normalized bytes 4-5. | VERIFIED |
| `tps_voltage` | `b[11]` | n/a | `b[11] * 5.0 / 256.0` | V | Decoder code; decoder tests; TPS sweep calibration verifies normalized byte 6. | VERIFIED |
| `tps_raw_candidate` | `b[12]` | n/a | `b[12]` | raw | Decoder code; canonical tests; TPS sweep calibration verifies normalized byte 7 as TPS raw. | VERIFIED |
| `battery_voltage` | `b[15]` | n/a | `b[15] / 10.0` | V | Decoder code; decoder tests; VOM calibration verifies normalized byte 14. | VERIFIED |
| `iat_c` | `b[16]` | n/a | `b[16] - 40` | degC | Decoder code; tests; calibration report marks normalized bytes 10-11 as IAT high-confidence. | PROVISIONAL |
| `ect_c_candidate` | `b[17]` | n/a | `b[17] - 40` | degC candidate | Decoder code and legacy tests preserve this value, but v0.2 calibration identifies normalized byte 13, not byte 12, as the engine-temperature byte. | UNKNOWN |
| `map_raw` | `b[18]` | n/a | `b[18]` | raw candidate | Decoder code and tests preserve this raw byte, but v0.2 calibration rejects normalized bytes 12-13 as MAP. | UNKNOWN |
| `signal_b19` | `b[19]` | n/a | raw byte | raw | Preserved by decoder only. | UNKNOWN |
| `signal_word_20_21` | `b[20]`, `b[21]` | big-endian u16 | `(b[20] << 8) | b[21]` | raw | Preserved by decoder only. | UNKNOWN |
| `signal_b22` | `b[22]` | n/a | raw byte | raw | Preserved by decoder only. | UNKNOWN |
| `signal_b23` | `b[23]` | n/a | raw byte | raw | Preserved by decoder only. | UNKNOWN |
| `signal_b24` | `b[24]` | n/a | raw byte | raw | Preserved by decoder only. | UNKNOWN |

### Runtime Validation Limits

Default limits from `app.config.SignalLimits`:

| Signal | Range |
| --- | --- |
| `rpm` | `0..16000` |
| `tps_voltage` | `0.0..5.0` |
| `tps_raw_candidate` | `0..255` |
| `battery_voltage` | `6.0..18.0` |
| `iat_c` | `-40..150` |
| `ect_c_candidate` | `-40..180` |
| `map_raw` | `0..255` |

## Audited Profile: `honda_keihin_71_17_v0.1` / `v0.2`

Module: `app.ecu.profiles.honda_keihin_71_17`

Frame contract:

| Field | Value |
| --- | --- |
| Normalized frame length | 24 bytes |
| Header | `02 18 71 17` |
| Checksum | `sum(frame) % 256 == 0` |
| Checksum byte | `(-sum(frame[:23])) & 0xFF` |
| Optional legacy normalization | 29-byte frame with five leading `FF` bytes -> drop prefix |

The profile decoder returns structured fields with status metadata. It separates
structural validity from semantic confidence and keeps uncertain fields as raw
candidates.

### Profile v0.2 Signal Table

Byte indexes below are zero-based indexes in the normalized 24-byte frame.

| Signal | Source byte(s) | Endian | Formula | Unit | Evidence | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| `rpm` | `b[4]`, `b[5]` | big-endian u16 | `(b[4] << 8) | b[5]` | rpm | Controlled off/start calibration; profile tests. | VERIFIED |
| `tps_voltage` | `b[6]` | n/a | `b[6] * 5 / 256` | V | Controlled TPS sweep, byte6 25..225. | VERIFIED |
| `tps_raw` | `b[7]` | n/a | raw | raw | Controlled TPS sweep, byte7 0..156, correlation with byte6. | VERIFIED |
| `tps_percent_calibrated` | `b[7]` | n/a | `(raw - 0) / (156 - 0) * 100`, clamped | percent | Endpoint calibration exists, but depends on observed endpoints for this vehicle. | PROVISIONAL |
| `iat` | `b[10]`, `b[11]` | n/a | `b[11] - 40`; `b[10]` voltage candidate | degC | Calibration report marks high confidence, not fully verified. | PROVISIONAL |
| `ect` | `b[12]`, `b[13]` | n/a | `b[13] - 40`; `b[12]` thermistor-voltage candidate | degC | Cold-start/warm-up calibration marks high confidence. | PROVISIONAL |
| `ect_legacy_8_9` | `b[8]`, `b[9]` | n/a | `FF FF` sentinel | n/a | Calibration shows bytes 8-9 remain `FF FF`. | UNKNOWN |
| `map_v01_hypothesis` | `b[12]`, `b[13]` | n/a | rejected old MAP hypothesis | n/a | Calibration rejects as MAP. | UNKNOWN |
| `battery` | `b[14]` | n/a | `b[14] / 10` | V | External VOM comparison. | VERIFIED |
| `injector_raw` | `b[15]`, `b[16]` | big-endian u16 | `(b[15] << 8) | b[16]` | raw | Calibration marks injector-related raw as high-confidence, raw scale only. | PROVISIONAL |
| `injector_ms` | `b[15]`, `b[16]` | big-endian u16 | candidate `raw / 100` | ms candidate | No oscilloscope or pulse-width reference. | UNKNOWN |
| `byte17` | `b[17]` | n/a | raw | raw | No physical identity assigned. | UNKNOWN |
| `byte18_speed` | `b[18]` | n/a | raw | raw | Rejected as direct vehicle speed in stationary calibration. | UNKNOWN |
| `unknown_reserved` | `b[19]..b[22]` | n/a | raw bytes | raw | Preserved only. | UNKNOWN |
| `checksum` | `b[23]` | n/a | two's-complement sum | byte | Checksum rule verified for valid frames. | VERIFIED |

## Decoder Evidence Inventory

Key evidence files in this repository:

- `app/ingestion/raw_ecu/decoder.py`
- `app/ingestion/raw_ecu/checksum.py`
- `app/ecu/profiles/honda_keihin_71_17.py`
- `tests/test_decoder.py`
- `tests/test_decoder_profile_71_17.py`
- `tests/test_checksum.py`
- `tests/test_canonical_pipeline.py`
- `tests/test_profile_v02_training.py`
- `data/decoder-audit/reports/calibration_audit.md`
- `data/decoder-audit/reports/calibration_signal_decisions.csv`
- `data/decoder-audit/profiles/honda_keihin_71_17_v0.2.json`

## Important Finding

The legacy canonical adapter still exposes V1 compatibility fields:

```text
rpm, tps_voltage, tps_raw_candidate, battery_voltage,
iat_c, ect_c_candidate, map_raw
```

The native Pi production adapter uses V2 fields:

```text
rpm, tps_voltage, tps_raw, battery_voltage, iat_c, ect_c
```

Production native provenance is `honda_keihin_71_17:1.0.0` with
`canonical-telemetry-v2`. V1 consumers must not assume `ect_c_candidate` or
`map_raw` has the same semantics as V2 `ect_c`.
