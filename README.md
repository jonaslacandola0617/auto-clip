# AutoClip

AutoClip is a local-first Tauri desktop application for turning long-form video into reviewable vertical clips. The React interface talks to an allowlisted JSON protocol served by the canonical Python processing layer; source media stays local and Gemini receives transcript text only when explicitly requested.

Phase 1B includes project persistence, local faster-whisper transcription, transcript corrections, manual and Gemini-assisted clip creation, clip trimming, structured captions, CPU-compatible reframing, 9:16 previews, MP4 rendering, and SRT/ASS/OTIO/Premiere XML exports.

## Development

Install the Python dependencies from `requirements-dev.txt` in a Python 3.12 virtual environment and install the JavaScript dependencies with pnpm. On Windows, Tauri also requires the Rust MSVC toolchain and Microsoft C++ Build Tools.

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest -q
pnpm test
pnpm build
pnpm tauri dev
```

Set `GEMINI_API_KEY` in the process environment or a local, ignored `.env` file to enable AI clip analysis. The key is never included in frontend state or canonical project files.

Representative spoken media is still required for live transcription, Gemini, framing, and final-render acceptance. Gate A, Gate B, and Gate C therefore remain provisional until those checks are completed.
