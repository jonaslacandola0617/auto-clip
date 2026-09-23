# ADR 0003 Export and Reframe Adapters

## Decision

Editable exports are read-only adapters over canonical timeline state. Resolve receives OpenTimelineIO JSON and Premiere receives XMEML v4. Unsupported dynamic reframe semantics produce explicit diagnostics instead of silent parity claims.

Reframing stores normalized source-space key points, confidence, detection state, smoothing settings, and a manual fixed-crop override. Proxy coordinates map deterministically back to source coordinates. Temporary subject loss holds the prior framing before easing toward center.

## Consequences

- NLE serializers are structurally testable without either target application.
- Physical import remains required before Gate B can pass.
- A replaceable detector can later use MediaPipe or OpenCV without changing canonical reframe data.

