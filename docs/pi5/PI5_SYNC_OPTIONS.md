# Pi5 Sync Options

Decision: Option C is implemented. Pi5 native sync now uses
`POST /api/device/sync/session` with existing device-token auth, canonical V2
records, imported Pi analysis, and strict Pi idempotency/conflict handling.

## Option A: Reuse `/api/logs/upload`

Pros:

- Existing device auth.
- Existing session/record persistence.
- Existing ACK shape.
- Existing frontend can display stored records.
- ESP behavior already proven by tests.

Cons:

- Semantics are ESP RAW batch upload, not Pi completed-session sync.
- It assumes Cloud-side `honda_keihin_legacy_29` profile when `raw_frame` is
  present.
- It cannot import a Pi-generated `AnalysisResult`.
- It has no content conflict detection for duplicate sequences.
- It stores session start/end wall-clock from server receive time unless Pi
  sends per-record timestamps.
- It does not clearly represent native 24-byte Pi frames versus 29-byte
  compatibility frames.

Assessment:

- Good for current ESP.
- Acceptable only if Pi pretends to be ESP and lets Cloud decode/analyze.
- Not a good semantic fit for Pi MVP where Pi has already decoded and analyzed
  locally.

## Option B: Extend Existing Browser Session APIs

Pros:

- Session pages and JSON endpoints already exist.
- Uses existing display model.

Cons:

- Browser APIs use Flask-Login, not device-token auth.
- They are read/display oriented, not offline sync oriented.
- Extending them for device writes would blur browser and device auth.

Assessment:

- Not the minimal practical path for Pi sync.
- Useful implementation reuse is in services/models, not the browser route
  surface.

## Option C: Dedicated Pi Sync API

Pros:

- Can reuse device-token auth without changing browser login.
- Can preserve ESP `/api/logs/upload` untouched.
- Can accept decoded/canonical telemetry directly.
- Can import Pi-generated analysis summary and provenance.
- Can define strict idempotency and conflict semantics.
- Can explicitly model 24-byte native RAW versus 29-byte compatibility RAW when
  optional RAW is synced.
- Can write existing `RideSession`, `TelemetryRecord`, and `AnalysisResult`
  rows so the current frontend remains useful.

Cons:

- Requires a new endpoint and validation/service layer.
- Requires careful contract tests to avoid weakening ESP behavior.

Implemented minimal practical path:

```text
Option C: add a dedicated authenticated Pi sync/import API.
```

It remains additive:

- Reuse `/api/device/pair`.
- Reuse device headers and token verification.
- Reuse existing DB tables where possible.
- Store Pi analysis instead of rerunning Cloud Analyzer by default.
- Add only the minimum schema/API surface needed for imported analysis
  provenance and idempotent conflict detection.

Implemented endpoint contract: `docs/pi5/PI5_SYNC_CONTRACT.md`.
