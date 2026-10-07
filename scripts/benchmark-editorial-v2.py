from __future__ import annotations

import argparse
import json
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from autoclip.ai import FixtureProvider
from autoclip.editorial_v2 import create_shorter_sequence_variant
from autoclip.intelligent_edit import construct_candidate_stories, plan_reviewed_sequence
from autoclip.media import FFmpegService
from autoclip.models import WorkflowProfile
from autoclip.pipeline import CorePipeline, chunk_transcript
from autoclip.production import select_diverse_edits
from autoclip.storage import Workspace, load_project


def measured(call):
    started = time.perf_counter()
    value = call()
    return value, round(time.perf_counter() - started, 6)


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark v1 and v2 editorial orchestration without retranscription.")
    parser.add_argument("project", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    workspace = Workspace(args.project.parent if args.project.is_file() else args.project)
    project = load_project(workspace)
    source, transcript = project.sources[0], project.transcripts[-1]
    cached_analysis = sorted((workspace.root / "analysis").glob("analysis-*.json"))
    if not cached_analysis:
        raise SystemExit("A cached v1 analysis response is required; this benchmark will not call a provider.")
    payload = json.loads(cached_analysis[-1].read_text(encoding="utf-8"))

    report: dict[str, object] = {
        "benchmark_version": "v2.1-benchmark-v1",
        "source": {"project": project.name, "transcript_revision": transcript.revision_id,
                   "duration_seconds": float(source.duration.seconds), "segment_count": len(transcript.segments),
                   "word_count": len(transcript.words)},
        "provider": {"mode": "cached-response replay", "network_requests": 0,
                     "disclosure": "Provider latency is not represented because no configured credential was available."},
    }

    with tempfile.TemporaryDirectory() as directory:
        legacy = CorePipeline(Workspace(Path(directory) / "legacy"), FFmpegService(), object(), FixtureProvider(payload), prompt_version="phase1b-v1")
        legacy_started = time.perf_counter()
        chunks, prep_seconds = measured(lambda: chunk_transcript(transcript))
        candidates, discovery_seconds = measured(lambda: legacy.analyze_source(source, transcript))
        (moments, stories), story_seconds = measured(lambda: construct_candidate_stories(candidates, transcript, source))
        moment_map = {item.id: item for item in moments}
        sequences, review_seconds = measured(lambda: [plan_reviewed_sequence(story, moment_map, transcript, source) for story in stories])
        profile = WorkflowProfile.from_dict(asdict(project.workflow_profiles[-1])) if project.workflow_profiles else WorkflowProfile("baseline", "Baseline")
        (selected, suppressed), diversity_seconds = measured(lambda: select_diverse_edits(sequences, {item.id: item for item in stories}, profile))
        legacy_total = round(time.perf_counter() - legacy_started, 6)
        report["baseline"] = {
            "cache_state": "cold orchestration with cached provider response replay",
            "total_analysis_seconds": legacy_total,
            "time_to_first_candidate_seconds": round(prep_seconds + discovery_seconds, 6),
            "conceptual_ai_request_count": 1,
            "actual_network_request_count": 0,
            "stage_seconds": {"transcript_chunking": prep_seconds, "candidate_discovery_and_ranking": discovery_seconds,
                              "moment_and_story_construction": story_seconds, "editorial_review_and_rebuild": review_seconds,
                              "diversity_deduplication": diversity_seconds},
            "context": {"chunks": len(chunks), "approximate_words": len(transcript.words)},
            "candidate_counts": {"discovered": len(candidates), "moments": len(moments), "stories": len(stories),
                                 "planned": len(sequences), "qualified": len(selected), "duplicates_suppressed": len(suppressed)},
            "candidate_durations_seconds": [round(float(item.source_end.seconds - item.source_start.seconds), 3) for item in candidates],
            "sequence_durations_seconds": [round(item.duration_seconds, 3) for item in sequences],
        }

        strict = WorkflowProfile(
            "v2_benchmark", "V2 Balanced Short", min_duration_seconds=12, max_duration_seconds=28,
            target_duration_seconds=20, preferred_max_duration_seconds=25, hard_max_duration_seconds=28,
            desired_output_count=5, pacing="fast", story_style="high-energy", analysis_mode="balanced",
        )
        provider = FixtureProvider(payload)
        modern = CorePipeline(Workspace(Path(directory) / "v2"), FFmpegService(), object(), provider, prompt_version="v2.1-editorial-schema-v1")
        (v2_candidates, windows, metrics), v2_wall = measured(lambda: modern.analyze_source_v2(source, transcript, profile=strict))
        (_warm_candidates, _warm_windows, warm_metrics), warm_wall = measured(lambda: modern.analyze_source_v2(source, transcript, profile=strict))
        previous_sequences = []
        for sequence in project.edit_sequences:
            if sequence.duration_seconds <= strict.duration_contract.hard_maximum:
                previous_sequences.append(sequence)
            else:
                shortened = create_shorter_sequence_variant(sequence, strict.duration_contract)
                if shortened:
                    previous_sequences.append(shortened)
        accepted, rejected_duplicates = select_diverse_edits(previous_sequences, {item.id: item for item in project.story_concepts}, strict)
        report["v2"] = {
            "cache_state": "cold orchestration with the same cached provider response replay",
            "wall_seconds": v2_wall,
            "metrics": asdict(metrics),
            "warm_cache": {"wall_seconds": warm_wall, "metrics": asdict(warm_metrics)},
            "candidate_window_count": len(windows),
            "qualified_highlight_count": len(v2_candidates),
            "qualified_highlight_durations_seconds": [round(float(item.source_end.seconds - item.source_start.seconds), 3) for item in v2_candidates],
            "strict_existing_sequence_acceptance": [{"title": item.title, "duration_seconds": round(item.duration_seconds, 3)} for item in accepted],
            "duplicates_suppressed": rejected_duplicates,
            "hard_maximum_seconds": strict.duration_contract.hard_maximum,
        }

    report["comparison_limitations"] = [
        "Both runs replay the same persisted provider response and therefore measure local orchestration, not Gemini latency.",
        "The v2 FAST and STRONG boundaries are exercised, but their calls are in-process fixture calls.",
        "A healthy-provider benchmark remains required before claiming the PRD latency targets pass.",
    ]
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
