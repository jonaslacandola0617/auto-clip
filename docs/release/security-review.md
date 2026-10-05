# AutoClip Version 1 security review

## Boundaries and mitigations

- The React UI can reach Python only through the versioned, allow-listed Tauri `worker_request` protocol. Unsupported commands and protocol mismatches fail closed.
- Release builds execute only the bundled frozen worker and bundled FFmpeg/ffprobe. They never search `PATH` for Python or media tools.
- Preview access is granted one canonical local video file at a time and only for supported media extensions. The Tauri asset scope is empty by default.
- Gemini credentials are encrypted with Windows DPAPI for the current user, written atomically, omitted from project files and diagnostics, and never returned to React.
- Diagnostics sanitize bearer tokens, common API-key forms, and user-profile paths. They exclude project media and transcripts.
- Cache deletion resolves and validates exact managed cache roots before removal. Projects and completed exports are not cache.
- Project and job writes use atomic replacement. Interrupted active jobs recover as failed/retryable rather than replaying automatically.
- A Windows Job Object plus an explicit application-exit shutdown terminates the processing worker when AutoClip closes.
- Media and rendering outputs use partial files followed by atomic replacement so failed operations do not masquerade as finished artifacts.

## Residual risks and release gates

- The executable and installer are not Authenticode-signed. Public distribution is blocked until signing and SmartScreen reputation validation are available.
- The bundled FFmpeg build is GPL-3.0-or-later. Public distribution is blocked until corresponding-source and license-delivery obligations are satisfied.
- The complete frozen-dependency license bundle and detector/model redistribution provenance require final compliance review.
- A disposable clean Windows VM test, malware scan, and installer reputation test remain required. Empty-`PATH` packaged-worker testing is strong evidence but is not a substitute for that VM gate.
- Automatic updating is intentionally disabled until a signed update channel and rollback procedure exist.
