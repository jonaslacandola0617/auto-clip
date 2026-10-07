from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from autoclip.desktop_service import DesktopService
from autoclip.editorial_v2 import ProviderFailure, quality_score
from autoclip.models import WorkflowProfile
from autoclip.pipeline import CorePipeline
from autoclip.media import FFmpegService
from autoclip.storage import Workspace, load_project


def candidate_summary(candidate) -> dict[str, object]:
    quality = candidate.editorial_quality
    return {
        "id": candidate.id,
        "title": candidate.title,
        "topic": candidate.category,
        "duration_seconds": round(float(candidate.source_end.seconds - candidate.source_start.seconds), 3),
        "hook": candidate.hook,
        "payoff": candidate.payoff,
        "story_structure": candidate.story_structure,
        "entertainment_score": quality.entertainment if quality else None,
        "overall_weighted_quality": quality_score(quality, "high-energy") if quality else None,
        "quality": asdict(quality) if quality else None,
        "source_ranges": [{"start": round(float(candidate.source_start.seconds), 3), "end": round(float(candidate.source_end.seconds), 3)}],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the V2.1 live-provider and immediate warm-cache acceptance benchmark.")
    parser.add_argument("project", type=Path)
    parser.add_argument("--credential-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prior-fast-503-retries", type=int, default=0)
    args = parser.parse_args()

    workspace = Workspace(args.project.parent if args.project.is_file() else args.project)
    project = load_project(workspace)
    source, transcript = project.sources[0], project.transcripts[-1]
    service = DesktopService(project_root=args.credential_root)
    settings = service.get_settings()
    provider = service._gemini_provider(settings)
    if not provider.available:
        raise SystemExit("Gemini is not configured through AutoClip's supported credential path.")
    profile = WorkflowProfile(
        "v2_live_acceptance", "High Energy V2.1 Acceptance",
        min_duration_seconds=12, max_duration_seconds=28,
        target_duration_seconds=20, preferred_max_duration_seconds=25, hard_max_duration_seconds=28,
        desired_output_count=5, pacing="fast", story_style="high-energy", analysis_mode="balanced",
    )
    pipeline = CorePipeline(workspace, FFmpegService(), object(), provider, prompt_version="v2.1-editorial-schema-v2")
    try:
        live_candidates, windows, live_metrics = pipeline.analyze_source_v2(source, transcript, profile=profile)
    except ProviderFailure as exc:
        failed = {
            "benchmark_version": "v2.1-live-provider-v1", "status": "provider_failed",
            "provider": {"name": type(provider).__name__, "model": provider.model},
            "source": {"project": project.name, "transcript_revision": transcript.revision_id, "transcription_invoked": False},
            "failure": {"kind": exc.kind, "message": str(exc)},
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(failed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(failed, indent=2))
        return 2
    warm_candidates, warm_windows, warm_metrics = pipeline.analyze_source_v2(source, transcript, profile=profile)
    report = {
        "benchmark_version": "v2.1-live-provider-v1",
        "provider": {
            "name": type(provider).__name__, "fast_model": provider.model, "strong_model": provider.model,
            "fast_configuration": "structured FAST window-ranking prompt; balanced mode; one bounded retry",
            "strong_configuration": "structured STRONG editorial-planning prompt; balanced mode; one bounded retry",
        },
        "provider_attempt_history": ({"initial_fast_503_retries": args.prior_fast_503_retries} if args.prior_fast_503_retries else {}),
        "source": {
            "project": project.name, "duration_seconds": float(source.duration.seconds),
            "transcript_revision": transcript.revision_id, "segment_count": len(transcript.segments),
            "word_count": len(transcript.words), "transcription_invoked": False,
        },
        "profile": asdict(profile.duration_contract) | {"requested_outputs": profile.desired_output_count, "style": profile.story_style},
        "live": {
            "metrics": asdict(live_metrics), "candidate_window_count": len(windows),
            "candidates": [candidate_summary(item) for item in live_candidates],
        },
        "warm": {
            "metrics": asdict(warm_metrics), "candidate_window_count": len(warm_windows),
            "same_candidate_ids": [item.id for item in live_candidates] == [item.id for item in warm_candidates],
            "candidates": [candidate_summary(item) for item in warm_candidates],
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output), "model": provider.model,
        "live_total_seconds": live_metrics.total_duration_seconds,
        "live_ai_requests": live_metrics.ai_request_count,
        "live_candidate_count": len(live_candidates),
        "warm_total_seconds": warm_metrics.total_duration_seconds,
        "warm_ai_requests": warm_metrics.ai_request_count,
        "same_candidate_ids": report["warm"]["same_candidate_ids"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
