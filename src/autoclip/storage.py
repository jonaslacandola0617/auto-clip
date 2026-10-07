from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from copy import deepcopy
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
    from .editor import ensure_editor_domain, validate_sequence

    workspace.ensure()
    ensure_editor_domain(project)
    for sequence in project.sequences:
        validate_sequence(project, sequence)
    snapshot = deepcopy(project)
    for sequence in snapshot.sequences:
        sequence.saved_revision = sequence.revision
    atomic_write_json(workspace.project_file, snapshot.to_dict())
    for sequence in project.sequences:
        sequence.saved_revision = sequence.revision


def load_project(workspace: Workspace) -> AutoClipProject:
    from .editor import ensure_editor_domain, validate_sequence

    try:
        project = AutoClipProject.from_dict(json.loads(workspace.project_file.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError):
        previous = workspace.project_file.with_suffix(workspace.project_file.suffix + ".previous")
        if not previous.exists():
            raise
        project = AutoClipProject.from_dict(json.loads(previous.read_text(encoding="utf-8")))
        project.legacy_compatibility["recovered_from_previous_snapshot"] = True
    project = ensure_editor_domain(project)
    for sequence in project.sequences:
        validate_sequence(project, sequence)
    return project


class AutosaveCoordinator:
    """Small debounce boundary; callers decide when to poll/flush it."""

    def __init__(self, workspace: Workspace, project: AutoClipProject, *, debounce_seconds: float = 0.5) -> None:
        self.workspace = workspace
        self.project = project
        self.debounce_seconds = max(0.0, debounce_seconds)
        self._due_at: float | None = None

    def schedule(self) -> None:
        self._due_at = time.monotonic() + self.debounce_seconds

    @property
    def pending(self) -> bool:
        return self._due_at is not None

    def flush(self, *, force: bool = False) -> bool:
        if self._due_at is None or (not force and time.monotonic() < self._due_at):
            return False
        save_project(self.workspace, self.project)
        self._due_at = None
        return True

