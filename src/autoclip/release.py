from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path


MODEL_APPROX_BYTES = {
    "tiny": 150 * 1024 * 1024,
    "base": 500 * 1024 * 1024,
    "small": 1_500 * 1024 * 1024,
    "medium": 3_200 * 1024 * 1024,
}

_SECRET_PATTERNS = (
    re.compile(r"((?:api[_-]?key|token|secret|authorization)\s*[:=]\s*)[^\s,;]+", re.IGNORECASE),
    re.compile(r"([?&](?:key|token|secret)=)[^&\s]+", re.IGNORECASE),
)


class InsufficientDiskSpaceError(RuntimeError):
    pass


def sanitize_diagnostic_text(value: str) -> str:
    sanitized = value
    for pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub(r"\1[redacted]", sanitized)
    return sanitized


def require_free_space(destination: Path, required_bytes: int, operation: str) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(destination).free
    if free < required_bytes:
        required_gb = required_bytes / (1024 ** 3)
        free_gb = free / (1024 ** 3)
        raise InsufficientDiskSpaceError(
            f"{operation} needs about {required_gb:.1f} GB free, but only {free_gb:.1f} GB is available. "
            "Free disk space or choose a project location on another drive."
        )
    return free


@dataclass(frozen=True, slots=True)
class ReleasePaths:
    state_root: Path

    @property
    def cache(self) -> Path:
        return self.state_root / "cache"

    @property
    def models(self) -> Path:
        return self.state_root / "models"

    @property
    def logs(self) -> Path:
        return self.state_root / "logs"

    @property
    def diagnostics(self) -> Path:
        return self.state_root / "diagnostics"

    def ensure(self) -> None:
        for path in (self.state_root, self.cache, self.models, self.logs, self.diagnostics):
            path.mkdir(parents=True, exist_ok=True)
