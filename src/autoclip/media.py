from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .time import MediaTime
from .models import MediaSource, StreamInfo
from .time import Rational, assess_frame_rate


class MediaToolError(RuntimeError):
    pass


def _project_local_static_binary(name: str) -> str | None:
    try:
        import static_ffmpeg
    except ImportError:
        return None
    suffix = ".exe" if os.name == "nt" else ""
    platform_directory = "win32" if os.name == "nt" else "linux"
    candidate = Path(static_ffmpeg.__file__).resolve().parent / "bin" / platform_directory / f"{name}{suffix}"
    return str(candidate) if candidate.exists() else None


def fingerprint_file(path: Path, *, sample_bytes: int = 1024 * 1024) -> dict[str, Any]:
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        digest.update(stream.read(sample_bytes))
        if stat.st_size > sample_bytes:
            stream.seek(max(0, stat.st_size - sample_bytes))
            digest.update(stream.read(sample_bytes))
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "partial_sha256": digest.hexdigest(), "basename": path.name}


def media_source_from_probe(source_id: str, path: Path, probe: dict[str, Any]) -> MediaSource:
    video = next((stream for stream in probe.get("streams", []) if stream.get("codec_type") == "video"), None)
    if video is None:
        raise MediaToolError("source has no video stream")
    time_base = Rational.parse(video["time_base"])
    assessment = assess_frame_rate(video.get("avg_frame_rate", video["r_frame_rate"]), video["r_frame_rate"])
    if "duration_ts" in video:
        duration = MediaTime(int(video["duration_ts"]), time_base)
    else:
        duration_seconds = probe.get("format", {}).get("duration")
        if duration_seconds is None:
            raise MediaToolError("source duration is unavailable")
        from fractions import Fraction
        ticks = Fraction(duration_seconds) / time_base.as_fraction()
        duration = MediaTime(round(float(ticks)), time_base)
    streams = [StreamInfo(
        index=int(stream["index"]), codec=stream.get("codec_name", "unknown"), kind=stream.get("codec_type", "unknown"),
        channels=int(stream["channels"]) if stream.get("channels") is not None else None,
        sample_rate=int(stream["sample_rate"]) if stream.get("sample_rate") else None,
    ) for stream in probe.get("streams", []) if stream.get("codec_type") in {"video", "audio"}]
    rotation = int(video.get("tags", {}).get("rotate", 0))
    return MediaSource(
        id=source_id, reference=str(path.absolute()), fingerprint=fingerprint_file(path), duration=duration,
        width=int(video["width"]), height=int(video["height"]), frame_rate=assessment.rate, time_base=time_base,
        streams=streams, rotation=rotation, variable_frame_rate=assessment.variable_frame_rate, vfr_fallback=assessment.fallback,
    )


@dataclass(slots=True)
class FFmpegService:
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"

    def __post_init__(self) -> None:
        if self.ffmpeg == "ffmpeg" and shutil.which(self.ffmpeg) is None:
            self.ffmpeg = _project_local_static_binary("ffmpeg") or self.ffmpeg
        if self.ffprobe == "ffprobe" and shutil.which(self.ffprobe) is None:
            self.ffprobe = _project_local_static_binary("ffprobe") or self.ffprobe

    @property
    def available(self) -> bool:
        return shutil.which(self.ffmpeg) is not None and shutil.which(self.ffprobe) is not None

    def _run(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(list(arguments), check=False, capture_output=True, text=True, encoding="utf-8")
        if result.returncode:
            raise MediaToolError(result.stderr.strip() or "media command failed")
        return result

    def inspect(self, source: Path) -> dict[str, Any]:
        result = self._run([self.ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source)])
        return json.loads(result.stdout)

    def extract_speech_audio(self, source: Path, output: Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        self._run([self.ffmpeg, "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output)])

    def extract_clip(self, source: Path, output: Path, start: MediaTime, end: MediaTime) -> None:
        if start.seconds < 0 or end.seconds <= start.seconds:
            raise ValueError("invalid clip range")
        output.parent.mkdir(parents=True, exist_ok=True)
        self._run([
            self.ffmpeg, "-y", "-ss", f"{float(start.seconds):.9f}", "-i", str(source),
            "-t", f"{float(end.seconds - start.seconds):.9f}", "-map", "0:v:0", "-map", "0:a?",
            "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", str(output),
        ])

    def create_lazy_proxy(self, source: Path, output: Path, *, width: int = 640) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        self._run([self.ffmpeg, "-y", "-i", str(source), "-vf", f"scale={width}:-2", "-c:v", "libx264", "-preset", "veryfast", "-an", str(output)])

    def render_vertical_preview(self, source: Path, output: Path, *, crop_x: int, crop_y: int, crop_width: int, crop_height: int) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        vf = f"crop={crop_width}:{crop_height}:{crop_x}:{crop_y},scale=1080:1920"
        self._run([self.ffmpeg, "-y", "-i", str(source), "-vf", vf, "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-c:a", "aac", str(output)])
