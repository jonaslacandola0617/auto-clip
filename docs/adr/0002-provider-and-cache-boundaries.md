# ADR 0002 Provider and Cache Boundaries

## Decision

Gemini, faster-whisper, and FFmpeg are infrastructure adapters behind stable interfaces. Gemini receives only bounded transcript chunks and explicit analysis metadata. Structured output is validated strictly before candidate construction or persistence.

Cache identities include source fingerprints and relevant model, transcript, provider, prompt, and configuration revisions. Successful transcription and analysis are authoritative stage results; extracted audio, proxies, previews, and rendered validation clips remain regenerable.

## Consequences

- Credentials are read from `GEMINI_API_KEY` and never serialized.
- Invalid AI timestamps fail before media execution.
- Re-extracting clips reuses valid transcription and analysis caches.
- Live validation can be deferred without blocking deterministic fixture tests.

