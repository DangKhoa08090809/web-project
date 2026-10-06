# Pi5 Signal Set Decision

## Decision

Selected V2 production core:

```text
rpm
tps_voltage
tps_raw
battery_voltage
iat_c
ect_c
```

This is Candidate B, the extended core.

## Evaluation Dataset

Existing repository data used:

- 9 files from `real_data/real_run`
- 3 calibration `raw_frames.jsonl` files from `real_data/calibration/honda_shmode_4v_001`

Total evaluated rows:

```text
43,989
```

Window settings:

```text
WINDOW_SIZE_SAMPLES = 50
WINDOW_STEP_SAMPLES = 10
```

## Candidate A Results

Candidate A:

```text
rpm
tps_voltage
tps_raw
battery_voltage
```

Measured using existing loader column `tps_raw_candidate`, equivalent to V2
`tps_raw`.

Results:

```text
eligible rows: 43,989 / 43,989
missing values: 0
non-finite values: 0
usable sessions: 12 / 12
generated windows: 4,345
feature rows: 4,345
feature count: 55
feature generation success sessions: 12 / 12
```

| Signal | Sessions with non-zero range | Observed min | Observed max |
| --- | ---: | ---: | ---: |
| `rpm` | 10 / 12 | 0.0 | 7780.0 |
| `tps_voltage` | 10 / 12 | 0.46875 | 4.39453125 |
| `tps_raw` | 10 / 12 | 0.0 | 156.0 |
| `battery_voltage` | 11 / 12 | 10.5 | 15.1 |

## Candidate B Results

Candidate B:

```text
rpm
tps_voltage
tps_raw
battery_voltage
iat_c
ect_c
```

Results:

```text
eligible rows: 43,989 / 43,989
missing values: 0
non-finite values: 0
usable sessions: 12 / 12
generated windows: 4,345
feature rows: 4,345
feature count: 81
feature generation success sessions: 12 / 12
```

| Signal | Sessions with non-zero range | Observed min | Observed max |
| --- | ---: | ---: | ---: |
| `rpm` | 10 / 12 | 0.0 | 7780.0 |
| `tps_voltage` | 10 / 12 | 0.46875 | 4.39453125 |
| `tps_raw` | 10 / 12 | 0.0 | 156.0 |
| `battery_voltage` | 11 / 12 | 10.5 | 15.1 |
| `iat_c` | 11 / 12 | 35.0 | 49.0 |
| `ect_c` | 10 / 12 | 36.0 | 93.0 |

## Why Candidate B Was Selected

Candidate B met the decision gate:

- `iat_c` and `ect_c` were consistently available across all evaluated rows.
- Both had zero missing and zero non-finite values.
- All 12 evaluated sessions were usable.
- Windowing and feature generation succeeded for all evaluated sessions.
- Calibration evidence marks both temperature signals high-confidence:
  `iat_c = byte11 - 40`, `ect_c = byte13 - 40`.

## Deliberately Excluded Signals

`map_raw` is excluded because the old MAP hypothesis was rejected by controlled
calibration. V2 must not make MAP a required signal until a real MAP location
and scale are established.

`tps_percent_calibrated` is excluded from required core because it depends on
vehicle-specific observed closed/open endpoints. It remains optional candidate
metadata.

`injector_ms`, `byte17`, and `byte18_speed` are excluded because physical
identity or scale is not sufficiently verified.

## Remaining Scientific Uncertainty

`iat_c` and `ect_c` are production-required for the V2 baseline because they are
complete and technically stable in current data, but their scientific confidence
remains PROVISIONAL/high-confidence rather than VERIFIED.

The V2 model remains an unsupervised anomaly baseline. No labelled diagnostic
accuracy is claimed.
