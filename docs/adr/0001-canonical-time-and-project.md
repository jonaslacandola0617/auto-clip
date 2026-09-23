# ADR 0001 Canonical Time and Project State

## Decision

AutoClip Phase 0 uses a versioned JSON project as authoritative state. Media timing is stored as integer ticks plus a rational seconds-per-tick time base. Human-readable seconds, frames, OTIO values, Premiere XML values, and FFmpeg arguments are derived views.

## Consequences

- NTSC rates use exact rationals such as 30000/1001 and 60000/1001.
- Export adapters cannot mutate the timeline.
- VFR detection records a timestamp-aware fallback and requires a clearly labeled normalized working representation before editable export when exact mapping cannot be guaranteed.
- Project writes use atomic replacement and preserve one previous snapshot.

