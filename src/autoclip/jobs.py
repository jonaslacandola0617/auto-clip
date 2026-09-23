from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .storage import atomic_write_json


JOB_STATES = {"queued", "running", "completed", "failed", "cancelled"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class Job:
    id: str
    type: str
    state: str
    progress: float
    current_stage: str
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    error: dict[str, Any] | None = None
    cancellable: bool = True
    project_path: str | None = None


JobRunner = Callable[[str, threading.Event, Callable[[float, str, bool | None], None]], None]


class JobManager:
    def __init__(self, store_path: Path) -> None:
        self.store_path = store_path
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._load()

    def _load(self) -> None:
        if not self.store_path.exists():
            return
        import json
        for raw in json.loads(self.store_path.read_text(encoding="utf-8")).get("jobs", []):
            job = Job(**raw)
            if job.state in {"queued", "running"}:
                job.state = "failed"
                job.completed_at = _now()
                job.cancellable = False
                job.error = {"code": "interrupted", "message": "The application closed before this job finished.", "recoverable": True}
            self._jobs[job.id] = job
        self._persist()

    def _persist(self) -> None:
        atomic_write_json(self.store_path, {"jobs": [asdict(job) for job in self._jobs.values()]})

    def start(self, job_type: str, runner: JobRunner, *, project_path: str | None = None, cancellable: bool = True) -> Job:
        job = Job(str(uuid.uuid4()), job_type, "queued", 0.0, "Waiting", _now(), cancellable=cancellable, project_path=project_path)
        event = threading.Event()
        with self._lock:
            self._jobs[job.id] = job
            self._cancel_events[job.id] = event
            self._persist()
        threading.Thread(target=self._run, args=(job.id, runner, event), daemon=True, name=f"autoclip-{job_type}").start()
        return self.get(job.id)

    def _run(self, job_id: str, runner: JobRunner, event: threading.Event) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if event.is_set():
                self._mark_cancelled(job)
                return
            job.state = "running"
            job.started_at = _now()
            job.current_stage = "Starting"
            self._persist()
        try:
            runner(job_id, event, lambda value, stage, cancellable=None: self.update(job_id, value, stage, cancellable))
            with self._lock:
                job = self._jobs[job_id]
                if event.is_set() and job.cancellable:
                    self._mark_cancelled(job)
                else:
                    job.state = "completed"
                    job.progress = 1.0
                    job.current_stage = "Complete"
                    job.completed_at = _now()
                    job.cancellable = False
                    self._persist()
        except Exception as exc:
            with self._lock:
                job = self._jobs[job_id]
                job.state = "failed"
                job.completed_at = _now()
                job.cancellable = False
                job.error = {"code": "job_failed", "message": str(exc) or "The job failed.", "recoverable": True}
                self._persist()

    def _mark_cancelled(self, job: Job) -> None:
        job.state = "cancelled"
        job.completed_at = _now()
        job.current_stage = "Cancelled"
        job.cancellable = False
        self._persist()

    def update(self, job_id: str, progress: float, stage: str, cancellable: bool | None = None) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.progress = max(0.0, min(1.0, progress))
            job.current_stage = stage
            if cancellable is not None:
                job.cancellable = cancellable
            self._persist()

    def cancel(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job.state not in {"queued", "running"}:
                return Job(**asdict(job))
            if not job.cancellable:
                raise RuntimeError("This job is at a stage that cannot be cancelled safely.")
            self._cancel_events[job_id].set()
            job.current_stage = "Cancelling safely"
            self._persist()
            return Job(**asdict(job))

    def get(self, job_id: str) -> Job:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            return Job(**asdict(self._jobs[job_id]))

    def list(self, project_path: str | None = None) -> list[Job]:
        with self._lock:
            jobs = [Job(**asdict(job)) for job in self._jobs.values() if project_path is None or job.project_path == project_path]
        return sorted(jobs, key=lambda job: job.created_at, reverse=True)
