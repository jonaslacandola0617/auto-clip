from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .time import MediaTime
from .models import EditSequence, EnhancementPlan, MediaSource, StreamInfo, VisualEditPlan
from .time import Rational, assess_frame_rate


class MediaToolError(RuntimeError):
    pass


class MediaOperationCancelled(MediaToolError):
    pass


@dataclass(frozen=True, slots=True)
class AudioEnergy:
    sample_count: int
    mean_db: float
    peak_db: float

    @property
    def audible(self) -> bool:
        return self.sample_count > 0 and self.mean_db > -55 and self.peak_db > -45


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

    def _run_cancellable(self, arguments: Sequence[str], cancel_event: threading.Event | None = None) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen(
            list(arguments), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8"
        )
        while True:
            if cancel_event is not None and cancel_event.is_set():
                process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate()
                raise MediaOperationCancelled(stderr.strip() or "media operation cancelled")
            try:
                stdout, stderr = process.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                continue
        result = subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)
        if result.returncode:
            raise MediaToolError(result.stderr.strip() or "media command failed")
        return result

    def inspect(self, source: Path) -> dict[str, Any]:
        result = self._run([self.ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source)])
        return json.loads(result.stdout)

    def measure_audio_energy(self, source: Path) -> AudioEnergy:
        result = self._run([
            self.ffmpeg, "-hide_banner", "-i", str(source), "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "NUL" if os.name == "nt" else "/dev/null",
        ])
        report = result.stderr
        samples = re.findall(r"n_samples:\s*(\d+)", report)
        mean = re.findall(r"mean_volume:\s*(-?inf|-?\d+(?:\.\d+)?)\s*dB", report)
        peak = re.findall(r"max_volume:\s*(-?inf|-?\d+(?:\.\d+)?)\s*dB", report)
        if not samples or not mean or not peak:
            raise MediaToolError("Rendered audio energy could not be measured.")
        parse_db = lambda value: float("-inf") if value == "-inf" else float(value)
        return AudioEnergy(int(samples[-1]), parse_db(mean[-1]), parse_db(peak[-1]))

    def validate_audible_audio(self, source: Path) -> AudioEnergy:
        energy = self.measure_audio_energy(source)
        if not energy.audible:
            raise MediaToolError(f"Rendered Smart Edit audio is silent or inaudible (mean {energy.mean_db:.1f} dB, peak {energy.peak_db:.1f} dB).")
        return energy

    def extract_speech_audio(self, source: Path, output: Path, *, cancel_event: threading.Event | None = None) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_name(f"{output.stem}.partial{output.suffix}")
        partial.unlink(missing_ok=True)
        try:
            self._run_cancellable(
                [self.ffmpeg, "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(partial)],
                cancel_event,
            )
            os.replace(partial, output)
        finally:
            partial.unlink(missing_ok=True)

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

    def render_vertical_clip(
        self,
        source: Path,
        output: Path,
        start: MediaTime,
        end: MediaTime,
        *,
        crop_x: int,
        crop_y: int,
        crop_width: int,
        crop_height: int,
        width: int = 1080,
        height: int = 1920,
        captions: Path | None = None,
    ) -> None:
        if start.seconds < 0 or end.seconds <= start.seconds:
            raise ValueError("invalid clip range")
        if output.exists():
            raise FileExistsError(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        filters = [f"crop={crop_width}:{crop_height}:{crop_x}:{crop_y}", f"scale={width}:{height}"]
        if captions:
            escaped = str(captions.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
            filters.append(f"ass='{escaped}'")
        self._run([
            self.ffmpeg, "-ss", f"{float(start.seconds):.9f}", "-i", str(source),
            "-t", f"{float(end.seconds - start.seconds):.9f}", "-vf", ",".join(filters),
            "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac", "-movflags", "+faststart", str(output),
        ])

    def plan_edit_sequence_render(self, source: Path, sequence: EditSequence, output: Path, *, width: int = 1080, height: int = 1920, captions: Path | None = None, visual_plan: VisualEditPlan | None = None, enhancement_plan: EnhancementPlan | None = None, graphics: Path | None = None) -> list[str]:
        if len(sequence.segments) < 2:
            raise ValueError("a Smart Edit render requires at least two segments")
        arguments = [self.ffmpeg, "-y"]
        render_ranges = visual_plan.shots if visual_plan and visual_plan.shots else sequence.segments
        for item in render_ranges:
            arguments.extend(["-ss", f"{float(item.source_in.seconds):.9f}", "-to", f"{float(item.source_out.seconds):.9f}", "-i", str(source)])
        assets = {item.id: item for item in enhancement_plan.assets} if enhancement_plan else {}
        broll_items = [item for item in (enhancement_plan.broll_items if enhancement_plan else []) if item.enabled and item.asset_id in assets and Path(assets[item.asset_id].reference).is_file()]
        sound_cues = [item for item in (enhancement_plan.sound_cues if enhancement_plan else []) if item.enabled and item.asset_id in assets and Path(assets[item.asset_id].reference).is_file()]
        music = enhancement_plan.music_track if enhancement_plan and enhancement_plan.music_track and enhancement_plan.music_track.enabled and enhancement_plan.music_track.asset_id in assets and Path(assets[enhancement_plan.music_track.asset_id].reference).is_file() else None
        input_index = len(render_ranges)
        broll_inputs: list[tuple[int, Any, Any]] = []
        for item in broll_items:
            asset = assets[item.asset_id]
            arguments.extend((["-loop", "1"] if asset.kind == "image" else ["-stream_loop", "-1"]) + ["-i", asset.reference])
            broll_inputs.append((input_index, item, asset)); input_index += 1
        sound_inputs: list[tuple[int, Any]] = []
        for item in sound_cues:
            arguments.extend(["-i", assets[item.asset_id].reference]); sound_inputs.append((input_index, item)); input_index += 1
        music_input: int | None = None
        if music:
            arguments.extend(["-stream_loop", "-1", "-i", assets[music.asset_id].reference]); music_input = input_index
        chains: list[str] = []
        concat_inputs = ""
        decisions = {item.id: item for item in visual_plan.reframe_decisions} if visual_plan else {}
        for index, item in enumerate(render_ranges):
            decision = decisions.get(getattr(item, "reframe_decision_id", ""))
            if decision and decision.layout == "split_screen" and len(decision.panels) >= 2:
                panel_filters = []
                for panel_index, panel in enumerate(decision.panels[:2]):
                    cx = panel.x + panel.width / 2
                    panel_filters.append(f"[sp{index}{panel_index}]crop=w='min(iw,trunc(ih*9/8/2)*2)':h=ih:x='max(0,min(iw-ow,{cx:.6f}*iw-ow/2))':y=0,scale={width}:{height//2}:force_original_aspect_ratio=increase,crop={width}:{height//2}[p{index}{panel_index}]")
                chains.append(f"[{index}:v]split=2[sp{index}0][sp{index}1]")
                chains.extend(panel_filters)
                chains.append(f"[p{index}0][p{index}1]vstack=inputs=2,setsar=1,setpts=PTS-STARTPTS[v{index}]")
            elif decision:
                scale = max(1.0, min(1.2, decision.scale))
                chains.append(f"[{index}:v]crop=w='min(iw,trunc(ih*9/16/{scale:.6f}/2)*2)':h='trunc(ih/{scale:.6f}/2)*2':x='max(0,min(iw-ow,{decision.crop_x:.6f}*iw-ow/2))':y='max(0,min(ih-oh,{decision.crop_y:.6f}*ih-oh*0.38))',scale={width}:{height},setsar=1,setpts=PTS-STARTPTS[v{index}]")
            else:
                chains.append(f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}:x=(in_w-out_w)/2:y=(in_h-out_h)/2,setsar=1,setpts=PTS-STARTPTS[v{index}]")
            chains.append(f"[{index}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS[a{index}]")
            concat_inputs += f"[v{index}][a{index}]"
        chains.append(f"{concat_inputs}concat=n={len(render_ranges)}:v=1:a=1[vcat][acat]")
        video_current = "vcat"
        for number, (index, item, _asset) in enumerate(broll_inputs):
            duration = item.timeline_end - item.timeline_start
            chains.append(f"[{index}:v]trim=duration={duration:.6f},setpts=PTS-STARTPTS+{item.timeline_start:.6f}/TB,scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1[broll{number}]")
            chains.append(f"[{video_current}][broll{number}]overlay=0:0:eof_action=pass[vb{number}]")
            video_current = f"vb{number}"
        if graphics:
            escaped_graphics = str(graphics.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
            chains.append(f"[{video_current}]ass='{escaped_graphics}'[vgraphics]")
            video_current = "vgraphics"
        if captions:
            escaped = str(captions.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
            chains.append(f"[{video_current}]ass='{escaped}'[vout]")
            video_map = "[vout]"
        else:
            video_map = f"[{video_current}]"
        audio_current = "acat"
        mix_labels: list[str] = []
        if music and music_input is not None:
            total = sequence.duration_seconds
            fade_out_start = max(0., total - music.fade_out)
            chains.append(f"[acat]asplit=2[dialogue][speechkey]")
            chains.append(f"[{music_input}:a]atrim=duration={total:.6f},asetpts=PTS-STARTPTS,volume={music.gain_db:.2f}dB,afade=t=in:st=0:d={music.fade_in:.3f},afade=t=out:st={fade_out_start:.3f}:d={music.fade_out:.3f}[musicraw]")
            chains.append("[musicraw][speechkey]sidechaincompress=threshold=0.03:ratio=8:attack=100:release=600[ducked]")
            audio_current = "dialogue"; mix_labels.append("ducked")
        for number, (index, item) in enumerate(sound_inputs):
            fade_out_start = max(0., item.duration - item.fade_seconds)
            chains.append(f"[{index}:a]atrim=duration={item.duration:.6f},asetpts=PTS-STARTPTS+{item.timeline_start:.6f}/TB,volume={item.gain_db:.2f}dB,afade=t=in:st=0:d={item.fade_seconds:.3f},afade=t=out:st={fade_out_start:.3f}:d={item.fade_seconds:.3f}[sfx{number}]")
            mix_labels.append(f"sfx{number}")
        if mix_labels:
            inputs = f"[{audio_current}]" + "".join(f"[{label}]" for label in mix_labels)
            chains.append(f"{inputs}amix=inputs={1+len(mix_labels)}:duration=first:normalize=0[aout]")
            audio_map = "[aout]"
        else:
            audio_map = "[acat]"
        arguments.extend(["-filter_complex", ";".join(chains), "-map", video_map, "-map", audio_map, "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(output)])
        return arguments

    def render_edit_sequence(self, source: Path, sequence: EditSequence, output: Path, *, width: int = 1080, height: int = 1920, captions: Path | None = None, visual_plan: VisualEditPlan | None = None, enhancement_plan: EnhancementPlan | None = None, graphics: Path | None = None, cancel_event: threading.Event | None = None) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_name(f"{output.stem}.partial{output.suffix}")
        partial.unlink(missing_ok=True)
        try:
            self._run_cancellable(self.plan_edit_sequence_render(source, sequence, partial, width=width, height=height, captions=captions, visual_plan=visual_plan, enhancement_plan=enhancement_plan, graphics=graphics), cancel_event)
            self.validate_audible_audio(partial)
            os.replace(partial, output)
        finally:
            partial.unlink(missing_ok=True)
