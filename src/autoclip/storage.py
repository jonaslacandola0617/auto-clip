from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import AutoClipProject


@dataclass(frozen=True, slots=True)
class Workspace:
    root: Path

    @property
    def project_file(self) -> Path:
        return self.root / "project.autoclip.json"

    def ensure(self) -> None:
        for relative in ("transcript", "analysis", "cache/audio", "cache/proxies", "cache/previews", "timelines", "exports"):
            (self.root / relative).mkdir(parents=True, exist_ok=True)

    def canonical_paths(self) -> set[Path]:
        return {self.project_file, self.root / "transcript", self.root / "analysis", self.root / "timelines"}

    def cache_key(self, stage: str, inputs: dict[str, Any]) -> str:
        payload = json.dumps({"stage": stage, "inputs": inputs}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def cache_result_path(self, stage: str, key: str) -> Path:
        base = "transcript" if stage == "transcription" else "analysis" if stage == "analysis" else "cache/previews"
        return self.root / base / f"{stage}-{key}.json"


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.with_suffix(path.suffix + ".previous")
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            previous.write_bytes(path.read_bytes())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def save_project(workspace: Workspace, project: AutoClipProject) -> None:
    workspace.ensure()
    atomic_write_json(workspace.project_file, project.to_dict())


def load_project(workspace: Workspace) -> AutoClipProject:
    return AutoClipProject.from_dict(json.loads(workspace.project_file.read_text(encoding="utf-8")))

