# AutoClip 1.0.0-rc.1 release validation

Validated October 5, 2026 on Windows x64.

## Artifacts

- NSIS installer: `AutoClip_1.0.0-rc.1_x64-setup.exe`
- Installer size: 392,834,192 bytes
- Installer SHA-256: `5581A22C9D2BDDBD30BF8AFA26992B713A450F386BCCC2D5882E54A4A232AF3A`
- Installed footprint: 672,981,565 bytes
- Packaged runtime footprint: 663,505,611 bytes
- Worker SHA-256: `386BF4059A03257D4EE7D8FD4FB9AB907E2D3C9A9562C40385C50D4591308CE4`
- Authenticode status: not signed (public-release blocker)

## Runtime and installation results

- Frozen worker passed with an empty `PATH`: Python 3.12.14, bundled FFmpeg 8.0.1, ffprobe, faster-whisper, OpenCV, packaged MediaPipe detector, and built-in OTIO export were available.
- First cold worker readiness check was approximately 22 seconds while Windows scanned the new 663 MB runtime; immediate warm check was approximately 3.6 seconds.
- Silent uninstall/install returned exit code 0 and preserved `%LOCALAPPDATA%\com.autoclip.desktop`.
- Installed application opened an `AutoClip` window, used approximately 32.8 MB working set at idle, launched the bundled `autoclip-worker.exe`, and launched no Python interpreter process.
- Normal window close terminated both desktop and worker processes. No orphan worker remained.
- Native screenshot automation was unavailable in the host, so responsive visual inspection at 1366x768 and 1920x1080 remains a manual acceptance item.

## Rights-safe media E2E

An 17.84-second source was generated locally from Windows synthetic speech and FFmpeg `testsrc2`. No third-party media was used.

- First transcription downloaded the managed `tiny` model and completed locally.
- A second run loaded the cached model without redownloading it.
- Transcription produced four timestamped segments.
- Manual clip creation, deterministic framing analysis, vertical preview, final render, and SRT export completed through the frozen worker with an empty `PATH`.
- Unicode project name/path (`Release E2E — Unicode ✓`) completed successfully.
- Final media probe: H.264, 1080x1920, AAC stereo, 48 kHz, 17.066 seconds. The Phase 2A decoded-audio validity gate passed during final rendering.

## Automated verification

- Python: 112 tests passed.
- Frontend: 12 tests passed across 4 files.
- TypeScript/Vite production build passed.
- `cargo check` passed.
- Tauri release build and NSIS bundle passed.
- Packaged worker validation and packaged rights-safe E2E passed.

## Remaining release gates

This artifact is suitable for internal RC evaluation, not public distribution. Required gates are Authenticode signing, FFmpeg GPL corresponding-source/license compliance, complete third-party license materials and asset provenance review, and a disposable clean-Windows-VM installation/upgrade/uninstall pass. No Phase 4 work is included.
