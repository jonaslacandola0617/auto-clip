from pathlib import Path

from autoclip.models import (
    ApprovedClip, AutoClipProject, CaptionCue, CaptionTrack, ClipCandidate, Marker,
    MediaSource, ReframeTrack, ScoreDimensions, StreamInfo, Timeline, Transcript,
    TranscriptSegment, TranscriptWord,
)
from autoclip.time import MediaTime, Rational
from autoclip.vision import build_reframe_track


RATE = Rational(30000, 1001)
TIME_BASE = Rational(1, 30000)


def mt(seconds: int) -> MediaTime:
    return MediaTime.from_seconds(seconds, TIME_BASE)


def sample_source(reference: str = "C:/fixtures/source.mp4") -> MediaSource:
    return MediaSource(
        id="media_001", reference=reference, fingerprint={"size": 12345, "partial_sha256": "fixture"},
        duration=mt(120), width=1920, height=1080, frame_rate=RATE, time_base=TIME_BASE,
        streams=[StreamInfo(0, "h264", "video"), StreamInfo(1, "aac", "audio", channels=2, sample_rate=48000)],
    )


def sample_transcript() -> Transcript:
    words = [
        TranscriptWord("w1", mt(10), mt(11), "This"), TranscriptWord("w2", mt(11), mt(12), "works"),
        TranscriptWord("w3", mt(40), mt(41), "Second"), TranscriptWord("w4", mt(41), mt(42), "moment"),
    ]
    segments = [
        TranscriptSegment("s1", mt(10), mt(12), "This works", ["w1", "w2"]),
        TranscriptSegment("s2", mt(40), mt(42), "Second moment", ["w3", "w4"]),
    ]
    return Transcript("transcript_001", "en", segments, words, "fixture")


def sample_candidate() -> ClipCandidate:
    return ClipCandidate(
        "candidate_001", mt(8), mt(28), "Useful moment", "This works", "education", "Complete thought",
        ScoreDimensions(90, 85, 80, 40), {"provider": "fixture", "model": "fixture"},
    )


def sample_timeline() -> Timeline:
    return Timeline(
        "timeline_001", 1080, 1920, RATE,
        [
            ApprovedClip("clip_001", "media_001", MediaTime.from_frames(240, RATE), MediaTime.from_frames(840, RATE), "First", "reframe_001", "caption_001", [Marker(MediaTime.from_frames(240, RATE), "hook")]),
            ApprovedClip("clip_002", "media_001", MediaTime.from_frames(1200, RATE), MediaTime.from_frames(1650, RATE), "Second"),
        ],
    )


def sample_project() -> AutoClipProject:
    timestamps = [mt(8), mt(9), mt(10)]
    reframe = build_reframe_track("reframe_001", timestamps, [None, None, None])
    caption = CaptionTrack("caption_001", [CaptionCue(mt(8), mt(10), "This works", ["w1", "w2"], "This really works")])
    return AutoClipProject(
        id="project_001", name="Fixture Project", sources=[sample_source()], transcripts=[sample_transcript()],
        candidates=[sample_candidate()], timelines=[sample_timeline()], reframe_tracks=[reframe], caption_tracks=[caption],
    )
