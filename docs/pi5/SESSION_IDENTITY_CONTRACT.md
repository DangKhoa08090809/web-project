# Session Identity Contract

## Device Identity

`Device.device_id` is the public device identifier sent by ESP headers.
`Device.id` is the internal DB primary key.

Device-token auth scopes ingestion to exactly one DB `Device`; regular browser
users can see only sessions for devices they own.

## Session Identity

`RideSession.session_id` is client supplied.

Uniqueness:

```text
RideSession:      unique(device_id, session_id)
TelemetryRecord:  unique(device_id, session_id, seq)
```

Therefore the same `session_id` may exist for different devices. The effective
identity is:

```text
authenticated device + session_id
```

`RideSession.id` is the internal browser/API primary key used by web routes.

## Sequence Identity

Each stored telemetry row is identified by:

```text
authenticated device + session_id + seq
```

`seq` must be a non-negative integer. The server does not require that the
first sequence be zero, but production ESP uses zero-based session-local
sequences.

## Duplicate And Conflict Behavior

Current idempotency:

- Duplicate rows with the same `(device, session_id, seq)` are ignored by
  database conflict handling.
- The response still returns success with updated `duplicate_count`.
- The server does not compare duplicate content.

Current conflict gap:

- If the same `(device, session_id, seq)` is resent with a different
  `raw_frame`, decoded values, or timestamp, Cloud silently keeps the original
  row and reports a duplicate.
- There is no session-level payload hash.
- `batch_id` is not unique and is not used for idempotency.

Required Pi sync semantics should be stricter:

```text
same identity + same content      -> success/idempotent
same identity + different content -> explicit conflict
```

## ACK Contract

`accepted_sequences` is derived from stored `TelemetryRecord.seq` values for
the authenticated device/session.

Fields:

- `minimum`: lowest stored sequence or null.
- `maximum`: highest stored sequence or null.
- `contiguous_until`: highest contiguous stored sequence starting from
  `minimum`.

Example:

```text
stored seqs = 0, 1, 2, 4
contiguous_until = 2
```

For normal ESP uploads beginning at sequence zero, this supports offline retry
and local deletion through `contiguous_until`.

## Finalization

When a batch has `session_ended = true`:

- `RideSession.ended_at` is set to the latest event time.
- If `contiguous_until == last_seq`, `sync_status = complete` and
  `capture_status = completed`.
- If the session has an end time but gaps remain, `sync_status = partial` and
  `capture_status = completed_with_gaps`.

If the session has no end marker, `sync_status = syncing` and
`capture_status = capturing`.

## Pi Implication

Pi can reuse the existing device-token identity model. For Pi MVP, the server
must add content-hash/conflict semantics around locally-created sessions and
their analysis artifacts, because Pi can be offline for long periods and may
retry larger completed session bundles.

