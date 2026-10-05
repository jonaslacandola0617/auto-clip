# AutoClip Version 1 release strategy

## Runtime and data layout

- Application binaries and the frozen Python worker install under the per-user AutoClip installation directory.
- Configuration, encrypted credentials, job state, logs, downloaded models, and diagnostics live under `%LOCALAPPDATA%\com.autoclip.desktop` as resolved by Tauri.
- User projects default to `%USERPROFILE%\Documents\AutoClip Projects` and remain ordinary portable folders.
- Project `cache` directories contain regenerable proxies, audio, and previews. `project.autoclip.json`, transcript corrections, timelines, manual overrides, and `exports` are authoritative and are never removed by cache cleanup.

## Runtime packaging

The processing layer is frozen as a PyInstaller one-directory worker. Tauri bundles that directory as application resources. FFmpeg and ffprobe come from the packaged `static-ffmpeg` distribution and release mode does not fall back to system PATH. Heavy ML models are initialized lazily; the selected faster-whisper model is downloaded on first transcription into AutoClip's managed model cache.

## Updates

Version 1 uses signed replacement installers when signing infrastructure is available. Automatic updates are intentionally deferred until a signed update channel can be operated and tested. Projects are versioned independently from the application and older supported schemas are migrated in memory before the next atomic save. Newer unsupported schemas are opened read-only only in future work; Version 1 currently refuses them without modifying the file.

## Release checklist

1. Build the worker with `npm run release:worker` in the pinned release environment.
2. Review `THIRD_PARTY_NOTICES.md`, the bundled FFmpeg license/configuration, and model metadata.
3. Build the NSIS installer with `npm run release:build`.
4. Sign the installer and executable when a Windows code-signing certificate is available.
5. Validate installation in a clean Windows VM with no Python, Node.js, Rust, pnpm, or FFmpeg on PATH.
6. Generate rights-safe media with `scripts/generate-release-fixture.ps1` and execute the release E2E checklist.
7. Record installer hash, installed size, startup timings, and results in the release record.

The current measured RC results and unresolved public-release gates are recorded in `validation-1.0.0-rc.1.md` and `security-review.md`.
