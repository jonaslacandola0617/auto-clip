from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


repository = Path(__file__).resolve().parents[1]
executable = repository / "src-tauri" / "resources" / "runtime" / "worker" / "autoclip-worker.exe"
environment = {
    "SYSTEMROOT": os.environ["SYSTEMROOT"],
    "WINDIR": os.environ["WINDIR"],
    "PATH": "",
    "AUTOCLIP_RELEASE": "1",
    "AUTOCLIP_APP_VERSION": "1.0.0-rc.1",
    "AUTOCLIP_APP_DATA": tempfile.mkdtemp(prefix="autoclip-frozen-worker-"),
}
request = {"version": "1", "id": "frozen-doctor", "command": "doctor", "payload": {}}
result = subprocess.run(
    [str(executable), "--stdio"],
    input=json.dumps(request) + "\n",
    text=True,
    encoding="utf-8",
    capture_output=True,
    env=environment,
    timeout=60,
    check=False,
)
print(f"returncode={result.returncode}")
print(result.stdout[-4000:])
if result.stderr:
    print(result.stderr[-4000:])
response = json.loads(result.stdout.strip())
if result.returncode != 0 or not response.get("ok"):
    raise SystemExit("Packaged worker doctor check failed.")
doctor = response["data"]
if not doctor["ffmpeg"]["ready"] or "Bundled" not in doctor["ffmpeg"]["summary"]:
    raise SystemExit("Packaged worker did not use bundled FFmpeg.")
if not doctor["mediapipe"]["ready"]:
    raise SystemExit("Packaged worker could not initialize the offline visual detector.")
print("PACKAGED_WORKER_OK")
