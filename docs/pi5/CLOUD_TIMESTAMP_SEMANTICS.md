# Cloud Timestamp Semantics

## Stored Fields

`TelemetryRecord` has three time-related fields:

- `timestamp`: optional wall-clock timestamp supplied by upload payload.
- `device_time_ms`: optional non-negative integer supplied by device.
- `timestamp_ms`: optional non-negative float used as canonical sample time.

Every row also has:

- `server_received_at`: Cloud wall-clock receive time.

## Durable ESP Upload

Production ESP sends `device_time_ms`, but the attached ESP contract says this
value is session-relative elapsed milliseconds from raw storage, not wall clock
and not boot uptime.

Current Cloud validation only checks that `device_time_ms` is a non-negative
integer. It does not reinterpret it as wall clock during validation.

## Session Wall-Clock Metadata

For durable upload, Cloud computes event time as:

```text
record.timestamp if present else record.server_received_at
```

That event time drives:

- initial `RideSession.started_at`
- `RideSession.last_record_at`
- final `RideSession.ended_at`
- dashboard session duration helpers

For normal ESP durable uploads without `timestamp`, session start/end are based
on Cloud receive time, not the capture time axis. Batched upload can therefore
make dashboard `duration` look like upload receive span instead of ride duration.

SEMANTIC MISMATCH: `device_time_ms` is session-relative elapsed time, but some
Cloud session-level wall-clock fields and dashboard duration labels are derived
from server receive time when no wall-clock `timestamp` is supplied.

## Analyzer And Chart Time Axis

For analyzer canonical samples:

```text
timestamp_ms = TelemetryRecord.timestamp_ms
            or TelemetryRecord.device_time_ms
            or None
```

For dashboard sample charts:

- `services.analysis.scored_samples` uses `device_time_ms / 1000` as elapsed
  seconds when present.
- If `device_time_ms` is missing, it falls back to wall-clock delta from event
  times.

So sample-level charts and Analyzer input handle ESP `device_time_ms` as a
relative session axis. The mismatch is mainly in session start/end/duration
metadata.

## Pi Implication

Pi sync should explicitly separate:

- session-local elapsed sample time;
- optional device wall-clock capture start/end;
- Cloud receive time.

If Pi has a reliable wall clock, it can sync wall-clock capture timestamps. If
not, the Cloud UI should not infer ride duration from receive time for completed
offline uploads.

