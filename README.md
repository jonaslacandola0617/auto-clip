# AutoClip Phase 0

This repository contains only the implementation-readiness spikes authorized for Phase 0. It provides canonical project/timeline models, exact media-time conversion, cache-safe pipeline services, editable timeline serializers, and a deterministic reframing track generator. It does not include the Tauri/React desktop product.

## Run the deterministic checks

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

Use `python -m autoclip.cli doctor` to inspect optional runtime integrations. Live media, transcription, Gemini, and NLE-import checks require the corresponding local tools, credentials, and representative rights-safe media.

