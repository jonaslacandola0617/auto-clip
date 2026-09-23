# AutoClip Phase 0.5 Live Integration Validation Report

## Environment validated

- Python 3.12.14
- FFmpeg and ffprobe 8.0.1, project-local static build
- faster-whisper 1.2.1
- OpenCV 5.0.0
- MediaPipe 1.0.1
- OpenTimelineIO 0.18.1
- CTranslate2 4.8.2 with zero CUDA devices
- Gemini REST adapter; no Gemini SDK required and `GEMINI_API_KEY` is not configured
- Intel UHD Graphics driver 31.0.101.4502; NVIDIA utilities and CUDA toolkit not detected
- DaVinci Resolve installed; Premiere Pro not detected

## Media used

No representative media was present in the repository or supplied attachments. Live media validation stopped at this required boundary.

## Gate status

- Gate A: PROVISIONAL. Runtime dependencies are ready, but no source video or Gemini credential was available.
- Gate B: PROVISIONAL. OTIO 0.18 and XML structural reads pass; Resolve GUI automation is unavailable and Premiere is not installed. Manual import is required.
- Gate C: PROVISIONAL. OpenCV and MediaPipe are installed, but no talking-head footage was available for detection or preview rendering.

## Bugs fixed

- Updated OTIO clips to the 0.18 `media_references` and `active_media_reference_key` schema after the real runtime rejected the earlier export.
- Added non-downloading discovery of project-local static FFmpeg and ffprobe binaries.
- Corrected the doctor command to report the resolved project-local binary paths.

## Tests executed

- Exporter and media command tests: passed.
- OpenTimelineIO runtime read: passed with two video and two audio items.
- Premiere XML parse: passed with two video and two audio items.

## Phase 1 decision

NO-GO. Gates A and C cannot pass without representative talking-head media, and Gate A additionally requires a configured Gemini credential.

