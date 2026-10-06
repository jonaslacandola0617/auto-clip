from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description="Exercise the packaged AutoClip worker with rights-safe media.")
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    worker = args.worker.resolve(strict=True)
    source = args.source.resolve(strict=True)
    root = args.workspace.resolve()
    app_data = root / "app-data"
    projects = root / "projects"
    app_data.mkdir(parents=True, exist_ok=True)
    projects.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update({
        "AUTOCLIP_RELEASE": "1",
        "AUTOCLIP_APP_VERSION": "1.0.0-rc.1",
        "AUTOCLIP_APP_DATA": str(app_data),
        "PATH": "",
    })
    process = subprocess.Popen(
        [str(worker), "--stdio"],
        cwd=worker.parent,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert process.stdin is not None and process.stdout is not None

    def request(command: str, payload: dict | None = None) -> dict:
        request_id = uuid.uuid4().hex
        process.stdin.write(json.dumps({"version": "1", "id": request_id, "command": command, "payload": payload or {}}) + "\n")
        process.stdin.flush()
        line = process.stdout.readline()
        if not line:
            stderr = process.stderr.read() if process.stderr else ""
            raise RuntimeError(f"Packaged worker stopped during {command}: {stderr[-2000:]}")
        response = json.loads(line)
        if response.get("id") != request_id or not response.get("ok"):
            raise RuntimeError(f"{command} failed: {response.get('error', response)}")
        return response["data"]

    def wait_job(job: dict) -> dict:
        deadline = time.monotonic() + args.timeout
        current = job
        last_stage = None
        while current["state"] not in {"completed", "failed", "cancelled"}:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Job {current['id']} timed out at {current.get('current_stage')}")
            if current.get("current_stage") != last_stage:
                print(f"{current['type']}: {current.get('current_stage')} ({current.get('progress', 0):.0%})", flush=True)
                last_stage = current.get("current_stage")
            time.sleep(1)
            current = request("get_job", {"job_id": current["id"]})
        if current["state"] != "completed":
            raise RuntimeError(f"Job {current['id']} ended as {current['state']}: {current.get('error')}")
        return current

    try:
        doctor = request("doctor")
        if not all(doctor[name]["ready"] for name in ("runtime", "storage", "ffmpeg", "ffprobe", "whisper")):
            raise RuntimeError(f"Packaged runtime readiness failed: {doctor}")
        request("update_settings", {"whisper_model": "tiny"})
        state = request("create_project", {
            "name": f"Release E2E — Unicode ✓ {uuid.uuid4().hex[:8]}",
            "location": str(projects),
            "source_path": str(source),
        })
        project_path = state["path"]
        wait_job(request("start_job", {"type": "transcribe", "project_path": project_path}))
        state = request("get_project_state", {"path": project_path})
        segments = state["transcript"]["segments"]
        if not segments or not any(segment["text"].strip() for segment in segments):
            raise RuntimeError("Transcription completed without usable text.")
        state = request("create_manual_clip", {
            "project_path": project_path,
            "start_segment_id": segments[0]["id"],
            "end_segment_id": segments[-1]["id"],
            "title": "Rights-safe release clip",
        })
        clip_id = state["clips"][0]["id"]
        wait_job(request("start_job", {"type": "reframe", "project_path": project_path, "clip_id": clip_id}))
        wait_job(request("start_job", {"type": "preview", "project_path": project_path, "clip_id": clip_id}))
        wait_job(request("start_job", {"type": "render", "project_path": project_path, "clip_id": clip_id}))
        wait_job(request("start_job", {"type": "export", "project_path": project_path, "clip_id": clip_id, "format": "srt"}))
        final_state = request("get_project_state", {"path": project_path})
        clip = next(item for item in final_state["clips"] if item["id"] == clip_id)
        render = Path(clip["render_path"])
        preview = Path(clip["preview_path"])
        if not render.is_file() or not preview.is_file():
            raise RuntimeError("Preview or final render was not created.")
        if not any(item["kind"] == "srt" and Path(item["path"]).is_file() for item in final_state["outputs"]):
            raise RuntimeError("SRT export was not created.")
        print(json.dumps({
            "status": "PASS",
            "project": project_path,
            "transcript_segments": len(segments),
            "preview": str(preview),
            "render": str(render),
            "outputs": [item["path"] for item in final_state["outputs"]],
            "model_cache": doctor["paths"]["models"],
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        if process.stdin:
            process.stdin.close()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
